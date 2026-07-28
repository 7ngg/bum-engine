using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Architecture;
using Autodesk.Revit.DB.Structure;

namespace BumEngine.Revit
{
    /// <summary>
    /// Host-agnostic model builder. Given a <see cref="LayoutModel"/> and an open
    /// Revit <see cref="Document"/>, it constructs native Walls, Rooms, Doors and
    /// Windows inside a single transaction, in metres. It makes NO UI calls, so the
    /// desktop add-in and the Design Automation engine invoke the exact same code.
    ///
    /// Targets the Revit 2025 API (.NET 8). Internal Revit units are feet; all
    /// layout values are metres and converted via <see cref="ToFeet"/>.
    /// </summary>
    public sealed class RevitBuilder
    {
        public sealed class BuildResult
        {
            public int Walls;
            public int Rooms;
            public int Doors;
            public int Windows;
            public int Terraces;
            public readonly List<string> Warnings = new();
            /// <summary>Non-failure diagnostics: which families/types were chosen,
            /// which sized types were created. Kept apart from Warnings so a clean
            /// build still reports what it actually placed.</summary>
            public readonly List<string> Notes = new();
            public string? SavedPath;
        }

        private static double ToFeet(double meters) =>
            UnitUtils.ConvertToInternalUnits(meters, UnitTypeId.Meters);

        /// <summary>Build the whole model. Returns element counts + warnings.</summary>
        public BuildResult Build(Document doc, LayoutModel layout, string? saveAsPath = null)
        {
            var result = new BuildResult();
            result.Warnings.AddRange(layout.Warnings);

            using (var tx = new Transaction(doc, "Build floor plan"))
            {
                tx.Start();
                TrySetMetresDisplayUnits(doc);

                // KNOWN BROKEN: schema advertises levels 1-4 (layout.Levels) but only
                // ever one Level at elevation 0 is created here — multi-storey layouts
                // are not built. Not fixed in this change.
                var level = GetOrCreateLevel(doc, 0.0);
                var wallHeightFt = ToFeet(layout.WallHeightM);

                var wallElems = CreateWalls(doc, layout, level, wallHeightFt, result);
                doc.Regenerate(); // let rooms see the wall enclosure

                CreateRooms(doc, layout, level, result);
                CreateTerrace(doc, layout, level, result);

                var doorSymbol = FindSymbol(doc, BuiltInCategory.OST_Doors, "door", result);
                var windowSymbol = FindSymbol(doc, BuiltInCategory.OST_Windows, "window", result);
                PlaceDoors(doc, layout, level, wallElems, doorSymbol, result);
                PlaceWindows(doc, layout, level, wallElems, windowSymbol, result);

                tx.Commit();
            }

            if (!string.IsNullOrEmpty(saveAsPath))
            {
                var opts = new SaveAsOptions { OverwriteExistingFile = true };
                doc.SaveAs(saveAsPath, opts);
                result.SavedPath = saveAsPath;
            }
            return result;
        }

        // ---- level ----------------------------------------------------------

        private static Level GetOrCreateLevel(Document doc, double elevationFt)
        {
            var existing = new FilteredElementCollector(doc)
                .OfClass(typeof(Level)).Cast<Level>()
                .FirstOrDefault(l => Math.Abs(l.Elevation - elevationFt) < 1e-6);
            return existing ?? Level.Create(doc, elevationFt);
        }

        // ---- walls ----------------------------------------------------------

        private Dictionary<string, Autodesk.Revit.DB.Wall> CreateWalls(
            Document doc, LayoutModel layout, Level level, double heightFt, BuildResult result)
        {
            var byId = new Dictionary<string, Autodesk.Revit.DB.Wall>();
            var typeCache = new Dictionary<double, WallType>();

            foreach (var w in layout.Walls)
            {
                var start = new XYZ(ToFeet(w.Start[0]), ToFeet(w.Start[1]), 0);
                var end = new XYZ(ToFeet(w.End[0]), ToFeet(w.End[1]), 0);
                if (start.DistanceTo(end) < 1e-6)
                {
                    result.Warnings.Add($"skipped zero-length wall {w.Id}");
                    continue;
                }
                var line = Line.CreateBound(start, end);
                var wallType = GetWallType(doc, w.ThicknessM, typeCache);
                var wall = Autodesk.Revit.DB.Wall.Create(
                    doc, line, wallType.Id, level.Id, heightFt, 0.0, false, /*structural*/ false);
                // slicer.py rasterises walls with `start`/`end` on the shared boundary
                // line between the two rooms' rects (see _build_walls in slicer.py —
                // Room.rect_m is written straight from that same rect, no thickness
                // clearance carved out), and revit/README.md documents walls[] as
                // "centerline" — so the curve passed above IS the centerline, not a
                // face. Pin that explicitly: the SDK default here must not be left to
                // infer, since the wrong choice silently doubles up wall thickness
                // into the adjoining rooms (or leaves a gap) without any error.
                var locParam = wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM);
                if (locParam != null && !locParam.IsReadOnly)
                    locParam.Set((int)WallLocationLine.WallCenterline);
                // exterior/interior tuning could set function via wallType; left default.
                byId[w.Id] = wall;
                result.Walls++;
            }
            return byId;
        }

        /// <summary>Duplicate a basic wall type to a single structural layer of the
        /// requested thickness (metres), cached per thickness.</summary>
        private WallType GetWallType(Document doc, double thicknessM, Dictionary<double, WallType> cache)
        {
            if (cache.TryGetValue(thicknessM, out var cached)) return cached;

            var baseType = new FilteredElementCollector(doc)
                .OfClass(typeof(WallType)).Cast<WallType>()
                .First(t => t.Kind == WallKind.Basic);

            var name = $"BUM {thicknessM:0.###}m";
            var dup = new FilteredElementCollector(doc).OfClass(typeof(WallType))
                          .Cast<WallType>().FirstOrDefault(t => t.Name == name)
                      ?? (WallType)baseType.Duplicate(name);

            var material = new FilteredElementCollector(doc).OfClass(typeof(Material))
                .Cast<Material>().FirstOrDefault();
            var cs = CompoundStructure.CreateSingleLayerCompoundStructure(
                MaterialFunctionAssignment.Structure, ToFeet(thicknessM),
                material?.Id ?? ElementId.InvalidElementId);
            dup.SetCompoundStructure(cs);

            cache[thicknessM] = dup;
            return dup;
        }

        // ---- rooms ----------------------------------------------------------

        private void CreateRooms(Document doc, LayoutModel layout, Level level, BuildResult result)
        {
            // ensure a phase exists for room placement
            var phase = doc.Phases.Cast<Phase>().LastOrDefault();
            int number = 1;
            foreach (var r in layout.Rooms)
            {
                var uv = new UV(ToFeet(r.CenterX), ToFeet(r.CenterY));
                try
                {
                    var room = doc.Create.NewRoom(level, uv);
                    if (room == null)
                    {
                        result.Warnings.Add($"room {r.Name} not enclosed; skipped");
                        continue;
                    }
                    room.Name = r.Name;
                    room.Number = number++.ToString();
                    result.Rooms++;
                }
                catch (Exception ex)
                {
                    result.Warnings.Add($"room {r.Name} failed: {ex.Message}");
                }
            }
        }

        // ---- terrace ----------------------------------------------------------

        private void CreateTerrace(Document doc, LayoutModel layout, Level level, BuildResult result)
        {
            var t = layout.Terrace;
            if (t == null) return;

            var floorType = new FilteredElementCollector(doc)
                .OfClass(typeof(FloorType)).Cast<FloorType>()
                .FirstOrDefault(ft => !ft.IsFoundationSlab);
            if (floorType == null)
            {
                result.Warnings.Add("no floor type available; terrace slab skipped");
                return;
            }

            var x0 = ToFeet(t.RectM[0]);
            var y0 = ToFeet(t.RectM[1]);
            var x1 = ToFeet(t.RectM[2]);
            var y1 = ToFeet(t.RectM[3]);
            var loop = CurveLoop.Create(new List<Curve>
            {
                Line.CreateBound(new XYZ(x0, y0, 0), new XYZ(x1, y0, 0)),
                Line.CreateBound(new XYZ(x1, y0, 0), new XYZ(x1, y1, 0)),
                Line.CreateBound(new XYZ(x1, y1, 0), new XYZ(x0, y1, 0)),
                Line.CreateBound(new XYZ(x0, y1, 0), new XYZ(x0, y0, 0)),
            });

            try
            {
                Floor.Create(doc, new List<CurveLoop> { loop }, floorType.Id, level.Id);
                result.Terraces++;
            }
            catch (Exception ex)
            {
                result.Warnings.Add($"terrace slab failed: {ex.Message}");
            }
        }

        // ---- openings -------------------------------------------------------

        // WHY THIS IS NOT AS SIMPLE AS SETTING A PARAMETER ON THE INSTANCE:
        // stock Revit door/window families carry Width and Height as TYPE
        // parameters on the FamilySymbol. A placed FamilyInstance has no such
        // parameter at all, so `inst.get_Parameter(DOOR_WIDTH)` returns null and
        // the set is a silent no-op. That is exactly how every opening in the
        // exported model ended up at its family's catalog default — windows came
        // out as "Fixed / 0406 x 0610 mm" (16" x 24"), far too small to read as
        // windows, while the layout asked for 1500 x 1200.
        // The only correct fix is to duplicate the symbol once per DISTINCT size,
        // set width/height on the duplicate, and place instances against it.

        // Width/Height live under different BuiltInParameters depending on how
        // the family author bound them; try each, then fall back to a by-name
        // lookup. Whichever resolves first wins, and a miss is reported loudly.
        private static readonly BuiltInParameter[] DoorWidthBips =
            { BuiltInParameter.DOOR_WIDTH, BuiltInParameter.FAMILY_WIDTH_PARAM };
        private static readonly BuiltInParameter[] DoorHeightBips =
            { BuiltInParameter.DOOR_HEIGHT, BuiltInParameter.FAMILY_HEIGHT_PARAM };
        private static readonly BuiltInParameter[] WindowWidthBips =
            { BuiltInParameter.WINDOW_WIDTH, BuiltInParameter.FAMILY_WIDTH_PARAM };
        private static readonly BuiltInParameter[] WindowHeightBips =
            { BuiltInParameter.WINDOW_HEIGHT, BuiltInParameter.FAMILY_HEIGHT_PARAM };

        private void PlaceDoors(
            Document doc, LayoutModel layout, Level level, Dictionary<string, Autodesk.Revit.DB.Wall> walls,
            FamilySymbol? symbol, BuildResult result)
        {
            if (symbol == null)
            {
                result.Warnings.Add("no door family available; doors skipped");
                return;
            }
            var all = new List<Door>(layout.Doors);
            if (layout.Entry != null && !string.IsNullOrEmpty(layout.Entry.WallId))
                all.Add(layout.Entry);

            var cache = new Dictionary<(int, int), FamilySymbol>();
            var legacyDoors = 0;
            var orientedDoors = 0;
            foreach (var d in all)
            {
                if (!walls.TryGetValue(d.WallId, out var host))
                {
                    result.Warnings.Add($"door {d.From}->{d.To} host wall {d.WallId} missing");
                    continue;
                }
                var sized = GetSizedSymbol(
                    doc, symbol, DoorWidthBips, DoorHeightBips,
                    d.WidthM, d.HeightM, cache, result);
                var loc = new XYZ(ToFeet(d.Center[0]), ToFeet(d.Center[1]), level.Elevation);
                var inst = doc.Create.NewFamilyInstance(loc, sized, host, level, StructuralType.NonStructural);
                ApplyDoorSwing(doc, inst, d, layout, result, ref legacyDoors, ref orientedDoors);
                result.Doors++;
            }
            if (legacyDoors > 0)
                result.Notes.Add(
                    $"{legacyDoors} door(s) carried no hinge/swing_into (layout schema " +
                    $"older than 1.2.0); left at Revit's derived hand/facing");
            result.Notes.Add(
                $"doors: {result.Doors} placed across {cache.Count} sized type(s) of " +
                $"family '{symbol.Family.Name}'");
            // PROOF OF EXECUTION, not just of intent. The old version of this
            // note printed the convention unconditionally, so it appeared even
            // when every door took the legacy early-return and ApplyDoorSwing
            // oriented nothing — which made it useless for answering "did the
            // deployed add-in actually run this code, or is it stale?".
            // orientedDoors counts doors that completed the orient path, so
            // "0/N oriented" is now an unambiguous negative.
            result.Notes.Add(
                $"door swing: {orientedDoors}/{result.Doors} doors oriented, hand convention " +
                $"{HandConvention}, family '{symbol.Family.Name}' — 0 oriented means this " +
                $"build did not apply swing data (stale add-in, or layout older than schema 1.2.0)");
            // Which binary is actually loaded. A human comparing a render to the
            // source needs this to rule out a stale DLL before reading anything
            // else in the plan as evidence.
            var asm = typeof(RevitBuilder).Assembly;
            var stamp = "unknown";
            try { stamp = System.IO.File.GetLastWriteTime(asm.Location).ToString("yyyy-MM-dd HH:mm:ss"); }
            catch { /* single-file / in-memory load: leave it unknown */ }
            result.Notes.Add($"builder assembly: {asm.GetName().Version} built {stamp}");
        }

        private void PlaceWindows(
            Document doc, LayoutModel layout, Level level, Dictionary<string, Autodesk.Revit.DB.Wall> walls,
            FamilySymbol? symbol, BuildResult result)
        {
            if (symbol == null)
            {
                result.Warnings.Add("no window family available; windows skipped");
                return;
            }
            var cache = new Dictionary<(int, int), FamilySymbol>();
            foreach (var wd in layout.Windows)
            {
                if (!walls.TryGetValue(wd.WallId, out var host))
                {
                    result.Warnings.Add($"window in {wd.Room} host wall {wd.WallId} missing");
                    continue;
                }
                var sized = GetSizedSymbol(
                    doc, symbol, WindowWidthBips, WindowHeightBips,
                    wd.WidthM, wd.HeightM, cache, result);
                var loc = new XYZ(ToFeet(wd.Center[0]), ToFeet(wd.Center[1]), level.Elevation + ToFeet(wd.SillM));
                var inst = doc.Create.NewFamilyInstance(loc, sized, host, level, StructuralType.NonStructural);
                // Sill height IS genuinely an instance parameter on stock windows —
                // it is the one of the three that was working before.
                SetLengthParam(
                    inst, new[] { BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM }, "Sill Height",
                    wd.SillM, $"window in {wd.Room} ({wd.WallId})", result);
                result.Windows++;
            }
            result.Notes.Add(
                $"windows: {result.Windows} placed across {cache.Count} sized type(s) of " +
                $"family '{symbol.Family.Name}'");
        }

        /// <summary>
        /// Which way a door family models HandOrientation relative to its hinge.
        ///
        /// The Revit API does NOT expose this: HandOrientation is just a vector,
        /// and whether a family authors it pointing hinge->far jamb (latch side)
        /// or hinge->near jamb is a decision baked into the family document. Two
        /// families that look identical in plan can disagree. So this is set
        /// EMPIRICALLY, once, per family — place one door, look at it in Revit,
        /// and if every door in the plan is mirrored, flip this one value.
        ///
        /// It is deliberately a single switch applied to every door: a mirrored
        /// family mirrors all of them together, so one flip must fix the whole
        /// plan at once. That is the property that makes the wrong guess cheap.
        /// </summary>
        public enum DoorHandConvention
        {
            /// <summary>HandOrientation points from the hinge toward the far (latch) jamb.</summary>
            HingeToFarJamb,
            /// <summary>HandOrientation points from the hinge toward the near jamb (mirrored family).</summary>
            HingeToNearJamb,
        }

        /// <summary>
        /// UNVERIFIED DEFAULT. This value has never been observed in Revit.
        ///
        /// The Revit API does not expose how a family models hand, so the only
        /// way to settle it is empirically, from the first render in which
        /// ApplyDoorSwing ACTUALLY RUNS. No render so far qualifies: the builds
        /// the architect reviewed all predate d88575d, the commit that
        /// introduced ApplyDoorSwing, and the add-in has never been deployed
        /// (no BumEngine add-in under either Revit Addins directory; the only
        /// build outputs in the tree are local compile checks). In those renders
        /// Revit derived hand and facing from the host wall alone, arbitrarily
        /// with respect to the room — which already explains both reported
        /// symptoms, leaves not folding against walls AND doors fouling each
        /// other, without any mirrored hand. So there was no mirrored render to
        /// infer this from, and neither value is currently supported by
        /// evidence.
        ///
        /// HOW TO SETTLE IT: deploy the add-in, confirm from the build Notes
        /// that doors were actually oriented (see the "door swing:" line — it
        /// reports oriented/total, and 0 oriented means this code did not run),
        /// then look at the plan. IF EVERY DOOR IS HINGED AT THE FAR END of its
        /// wall — leaf opening into the middle of the room instead of folding
        /// back against the return wall — FLIP THIS VALUE to HingeToFarJamb.
        /// If only some doors are wrong, the convention is not the variable and
        /// the fault lies elsewhere; do not flip it.
        ///
        /// Whatever the answer, it is evidence about ONE family, not a
        /// universal truth, which is why this stays configurable.
        /// </summary>
        public DoorHandConvention HandConvention { get; set; } = DoorHandConvention.HingeToNearJamb;

        /// <summary>
        /// Orient a placed door so it matches the layout's hinge + swing_into.
        ///
        /// Schema 1.2.0 added those two fields precisely because they did not
        /// exist before: with no hinge or facing in layout.json this builder had
        /// nothing to apply, so Revit derived hand and facing from the host wall
        /// alone — uniformly, and arbitrarily with respect to the room. That is
        /// the "qapilar divara acilmir" / colliding-swings complaint.
        ///
        /// FACING is MEASURED, never assumed. FacingOrientation is a real
        /// world-space vector and the swing_into room's centroid is a real
        /// world-space point, so the question "does this leaf sweep into that
        /// room?" is answered by one dot product against the vector from the
        /// door to that centroid — no family convention enters into it. The
        /// wall is no longer consulted for facing at all (it used to supply a
        /// normal to compare against, which was correct but was a derivation
        /// that could be got wrong for a door whose host wall is mis-identified).
        /// The read-back re-tests that same geometric truth, so unlike the hand
        /// check it proves the door really does open into the right room.
        ///
        /// HAND remains a STATED CONVENTION (<see cref="HandConvention"/>),
        /// because nothing in the API exposes how a family models it. Note what
        /// the hand read-back can and cannot prove: it re-tests HandOrientation
        /// against the SAME assumed vector, so it verifies the flip took effect,
        /// not that the convention is right. A mirrored family passes it and
        /// still renders every leaf on the wrong jamb. Only a human looking at a
        /// rendered plan can falsify that bit — which is exactly how the current
        /// default was arrived at.
        /// </summary>
        private void ApplyDoorSwing(
            Document doc, FamilyInstance inst, Door d, LayoutModel layout,
            BuildResult result, ref int legacyDoors, ref int orientedDoors)
        {
            if (!d.HasSwing)
            {
                legacyDoors++;   // older schema: leave Revit's own derivation alone
                return;
            }

            var wall = layout.Walls.FirstOrDefault(w => w.Id == d.WallId);
            if (wall == null)
            {
                result.Warnings.Add($"SWING SKIPPED: door {d.From}->{d.To} host wall {d.WallId} not in layout");
                return;
            }
            var target = TargetCentre(layout, d.SwingInto!);
            if (target == null)
            {
                result.Warnings.Add(
                    $"SWING SKIPPED: door {d.From}->{d.To} swings into '{d.SwingInto}', " +
                    $"which is neither a room nor the terrace");
                return;
            }

            // wall direction — needed for HAND only; facing no longer uses it.
            double ux = wall.End[0] - wall.Start[0], uy = wall.End[1] - wall.Start[1];
            var len = Math.Sqrt(ux * ux + uy * uy);
            if (len < 1e-9)
            {
                result.Warnings.Add($"SWING SKIPPED: door {d.From}->{d.To} host wall {d.WallId} is zero-length");
                return;
            }
            ux /= len; uy /= len;

            // The direction the leaf must sweep: door centre -> centre of the
            // room named by swing_into. World-space, measured, convention-free.
            var toRoom = new XYZ(
                ToFeet(target.Value.X - d.Center[0]),
                ToFeet(target.Value.Y - d.Center[1]), 0);
            if (toRoom.GetLength() < 1e-9)
            {
                result.Warnings.Add(
                    $"SWING SKIPPED: door {d.From}->{d.To} sits exactly on the centre of " +
                    $"'{d.SwingInto}'; no facing direction can be derived");
                return;
            }

            // hand: from the hinge jamb toward the far jamb, under the family's
            // convention. hinge=="start" means the leaf hangs at wall.Start, so
            // the far jamb lies along +u; HingeToNearJamb mirrors that.
            var sign = string.Equals(d.Hinge, "start", StringComparison.OrdinalIgnoreCase) ? 1.0 : -1.0;
            if (HandConvention == DoorHandConvention.HingeToNearJamb) sign = -sign;
            var wantHand = new XYZ(ux * sign, uy * sign, 0);

            doc.Regenerate(); // orientations are only meaningful after regeneration

            // FACING: dot(FacingOrientation, toRoom) > 0 means the leaf already
            // sweeps toward the room it is supposed to open into. <= 0 includes
            // the exactly-perpendicular case, which is not "good enough" — it
            // means the reading is degenerate and the flip should still be tried.
            var facingDotBefore = inst.FacingOrientation.DotProduct(toRoom);
            if (facingDotBefore <= 0)
            {
                if (inst.CanFlipFacing) inst.flipFacing();
                else result.Warnings.Add($"SWING: door {d.From}->{d.To} cannot flip facing (family forbids it)");
            }
            if (inst.HandOrientation.DotProduct(wantHand) < 0)
            {
                if (inst.CanFlipHand) inst.flipHand();
                else result.Warnings.Add($"SWING: door {d.From}->{d.To} cannot flip hand (family forbids it)");
            }

            doc.Regenerate();
            orientedDoors++;   // reached the orient path and applied it

            // A flip returning without throwing is not proof it took — read back.
            // For facing this re-tests the GEOMETRIC truth (does the leaf sweep
            // into the room?), so a pass here is a real guarantee, not a
            // restatement of an assumption.
            var facingDotAfter = inst.FacingOrientation.DotProduct(toRoom);
            if (facingDotAfter <= 0)
                result.Warnings.Add(
                    $"SWING DID NOT STICK: door {d.From}->{d.To} ({d.WallId}) must open into " +
                    $"'{d.SwingInto}' (direction {Fmt(toRoom)}) but faces {Fmt(inst.FacingOrientation)} " +
                    $"— dot {facingDotBefore:0.###} before flip, {facingDotAfter:0.###} after");
            if (inst.HandOrientation.DotProduct(wantHand) < 0)
                result.Warnings.Add(
                    $"SWING DID NOT STICK: door {d.From}->{d.To} ({d.WallId}) hinge '{d.Hinge}' " +
                    $"wants hand {Fmt(wantHand)} ({HandConvention}) but the model reads " +
                    $"{Fmt(inst.HandOrientation)}");
        }

        /// <summary>Centre of the room named by swing_into, or of the terrace.</summary>
        private static (double X, double Y)? TargetCentre(LayoutModel layout, string name)
        {
            var room = layout.Rooms.FirstOrDefault(r => r.Name == name);
            if (room != null) return (room.CenterX, room.CenterY);
            if (layout.Terrace != null && name == "Terrace")
            {
                var t = layout.Terrace.RectM;
                return ((t[0] + t[2]) / 2.0, (t[1] + t[3]) / 2.0);
            }
            return null;
        }

        private static string Fmt(XYZ v) => $"({v.X:0.##},{v.Y:0.##})";

        /// <summary>
        /// Return a FamilySymbol of <paramref name="baseSymbol"/>'s family sized
        /// exactly width x height metres, creating (and caching) one duplicated
        /// type per distinct size. Type names follow Revit convention —
        /// "1500 x 1200 mm" — so the size is legible in the Project Browser.
        /// Falls back to the base symbol only if sizing is impossible, and says so.
        /// </summary>
        private FamilySymbol GetSizedSymbol(
            Document doc, FamilySymbol baseSymbol,
            BuiltInParameter[] widthBips, BuiltInParameter[] heightBips,
            double widthM, double heightM,
            Dictionary<(int, int), FamilySymbol> cache, BuildResult result)
        {
            var wMm = (int)Math.Round(widthM * 1000.0);
            var hMm = (int)Math.Round(heightM * 1000.0);
            var key = (wMm, hMm);
            if (cache.TryGetValue(key, out var cached)) return cached;

            var typeName = $"{wMm} x {hMm} mm";
            var family = baseSymbol.Family;

            // Re-running against a document that already holds our types must
            // reuse them, not throw on the duplicate name.
            var sym = family.GetFamilySymbolIds()
                .Select(id => doc.GetElement(id))
                .OfType<FamilySymbol>()
                .FirstOrDefault(s => s.Name == typeName);

            var created = false;
            if (sym == null)
            {
                try
                {
                    sym = (FamilySymbol)baseSymbol.Duplicate(typeName);
                    created = true;
                }
                catch (Exception ex)
                {
                    result.Warnings.Add(
                        $"SIZING FAILED: could not duplicate '{family.Name}' type for " +
                        $"{typeName}: {ex.Message} — falling back to catalog default " +
                        $"'{baseSymbol.Name}', openings of this size will be the WRONG SIZE");
                    EnsureActive(doc, baseSymbol);
                    cache[key] = baseSymbol;
                    return baseSymbol;
                }
            }

            var label = $"{family.Name} / {typeName}";
            var okW = SetLengthParam(sym, widthBips, "Width", widthM, label, result);
            var okH = SetLengthParam(sym, heightBips, "Height", heightM, label, result);
            doc.Regenerate(); // let formula/constraint-driven families settle first

            // Set() returning true is not proof: a constrained or formula-driven
            // parameter can accept the write and then snap back. Read it back.
            VerifyLengthParam(sym, widthBips, "Width", widthM, label, okW, result);
            VerifyLengthParam(sym, heightBips, "Height", heightM, label, okH, result);

            EnsureActive(doc, sym);
            if (created)
                result.Notes.Add($"created opening type '{label}'");
            cache[key] = sym;
            return sym;
        }

        // ---- helpers --------------------------------------------------------

        private static FamilySymbol? FindSymbol(
            Document doc, BuiltInCategory category, string what, BuildResult result)
        {
            var sym = new FilteredElementCollector(doc)
                .OfClass(typeof(FamilySymbol))
                .OfCategory(category)
                .Cast<FamilySymbol>()
                .FirstOrDefault();
            // Selection is still "first symbol of the category in the template" —
            // deliberately unchanged here, but no longer invisible: report it so a
            // surprising family (e.g. a non-opening "Fixed" window) is obvious.
            if (sym != null)
                result.Notes.Add($"{what} base family: '{sym.Family.Name}' (base type '{sym.Name}')");
            return sym;
        }

        private static void EnsureActive(Document doc, FamilySymbol symbol)
        {
            if (!symbol.IsActive)
            {
                symbol.Activate();
                doc.Regenerate();
            }
        }

        /// <summary>Set a length parameter (metres in, feet stored), trying each
        /// BuiltInParameter then a by-name lookup. Every failure mode is reported —
        /// a silently discarded return value is what hid the opening-size defect.</summary>
        private static bool SetLengthParam(
            Element e, BuiltInParameter[] bips, string paramName,
            double meters, string label, BuildResult result)
        {
            Parameter? p = null;
            foreach (var bip in bips)
            {
                p = e.get_Parameter(bip);
                if (p != null) break;
            }
            p ??= e.LookupParameter(paramName);

            if (p == null)
            {
                result.Warnings.Add($"PARAM MISSING: '{paramName}' not found on {label}");
                return false;
            }
            if (p.IsReadOnly)
            {
                result.Warnings.Add($"PARAM READ-ONLY: '{paramName}' on {label}");
                return false;
            }
            if (p.StorageType != StorageType.Double)
            {
                result.Warnings.Add(
                    $"PARAM WRONG TYPE: '{paramName}' on {label} is {p.StorageType}, expected Double");
                return false;
            }
            if (!p.Set(ToFeet(meters)))
            {
                result.Warnings.Add(
                    $"PARAM SET REJECTED: '{paramName}' = {meters:0.###} m on {label}");
                return false;
            }
            return true;
        }

        /// <summary>Read a length parameter back after regeneration and report any
        /// drift from what was requested.</summary>
        private static void VerifyLengthParam(
            Element e, BuiltInParameter[] bips, string paramName,
            double meters, string label, bool wasSet, BuildResult result)
        {
            if (!wasSet) return; // SetLengthParam already reported the failure
            Parameter? p = null;
            foreach (var bip in bips)
            {
                p = e.get_Parameter(bip);
                if (p != null) break;
            }
            p ??= e.LookupParameter(paramName);
            if (p == null) return;

            var actualM = UnitUtils.ConvertFromInternalUnits(p.AsDouble(), UnitTypeId.Meters);
            if (Math.Abs(actualM - meters) > 1e-4)
            {
                result.Warnings.Add(
                    $"PARAM DID NOT STICK: '{paramName}' on {label} requested " +
                    $"{meters:0.###} m, model reads {actualM:0.###} m");
            }
        }

        private static void TrySetMetresDisplayUnits(Document doc)
        {
            try
            {
                var units = doc.GetUnits();
                var fo = new FormatOptions(UnitTypeId.Meters);
                units.SetFormatOptions(SpecTypeId.Length, fo);
                doc.SetUnits(units);
            }
            catch { /* cosmetic only */ }
        }
    }
}
