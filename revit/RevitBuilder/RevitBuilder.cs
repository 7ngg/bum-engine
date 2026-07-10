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
            public readonly List<string> Warnings = new();
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

                var level = GetOrCreateLevel(doc, 0.0);
                var wallHeightFt = ToFeet(layout.WallHeightM);

                var wallElems = CreateWalls(doc, layout, level, wallHeightFt, result);
                doc.Regenerate(); // let rooms see the wall enclosure

                CreateRooms(doc, layout, level, result);

                var doorSymbol = FindSymbol(doc, BuiltInCategory.OST_Doors);
                var windowSymbol = FindSymbol(doc, BuiltInCategory.OST_Windows);
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

        private Dictionary<string, Wall> CreateWalls(
            Document doc, LayoutModel layout, Level level, double heightFt, BuildResult result)
        {
            var byId = new Dictionary<string, Wall>();
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

        // ---- openings -------------------------------------------------------

        private void PlaceDoors(
            Document doc, LayoutModel layout, Level level, Dictionary<string, Wall> walls,
            FamilySymbol? symbol, BuildResult result)
        {
            if (symbol == null)
            {
                result.Warnings.Add("no door family available; doors skipped");
                return;
            }
            EnsureActive(doc, symbol);
            var all = new List<Door>(layout.Doors);
            if (layout.Entry != null && !string.IsNullOrEmpty(layout.Entry.WallId))
                all.Add(layout.Entry);

            foreach (var d in all)
            {
                if (!walls.TryGetValue(d.WallId, out var host))
                {
                    result.Warnings.Add($"door {d.From}->{d.To} host wall {d.WallId} missing");
                    continue;
                }
                var loc = new XYZ(ToFeet(d.Center[0]), ToFeet(d.Center[1]), level.Elevation);
                var inst = doc.Create.NewFamilyInstance(loc, symbol, host, level, StructuralType.NonStructural);
                TrySetParam(inst, BuiltInParameter.DOOR_WIDTH, ToFeet(d.WidthM));
                TrySetParam(inst, BuiltInParameter.DOOR_HEIGHT, ToFeet(d.HeightM));
                result.Doors++;
            }
        }

        private void PlaceWindows(
            Document doc, LayoutModel layout, Level level, Dictionary<string, Wall> walls,
            FamilySymbol? symbol, BuildResult result)
        {
            if (symbol == null)
            {
                result.Warnings.Add("no window family available; windows skipped");
                return;
            }
            EnsureActive(doc, symbol);
            foreach (var wd in layout.Windows)
            {
                if (!walls.TryGetValue(wd.WallId, out var host))
                {
                    result.Warnings.Add($"window in {wd.Room} host wall {wd.WallId} missing");
                    continue;
                }
                var loc = new XYZ(ToFeet(wd.Center[0]), ToFeet(wd.Center[1]), level.Elevation + ToFeet(wd.SillM));
                var inst = doc.Create.NewFamilyInstance(loc, symbol, host, level, StructuralType.NonStructural);
                TrySetParam(inst, BuiltInParameter.WINDOW_WIDTH, ToFeet(wd.WidthM));
                TrySetParam(inst, BuiltInParameter.WINDOW_HEIGHT, ToFeet(wd.HeightM));
                TrySetParam(inst, BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM, ToFeet(wd.SillM));
                result.Windows++;
            }
        }

        // ---- helpers --------------------------------------------------------

        private static FamilySymbol? FindSymbol(Document doc, BuiltInCategory category)
        {
            return new FilteredElementCollector(doc)
                .OfClass(typeof(FamilySymbol))
                .OfCategory(category)
                .Cast<FamilySymbol>()
                .FirstOrDefault();
        }

        private static void EnsureActive(Document doc, FamilySymbol symbol)
        {
            if (!symbol.IsActive)
            {
                symbol.Activate();
                doc.Regenerate();
            }
        }

        private static void TrySetParam(Element e, BuiltInParameter bip, double value)
        {
            var p = e.get_Parameter(bip);
            if (p != null && !p.IsReadOnly) p.Set(value);
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
