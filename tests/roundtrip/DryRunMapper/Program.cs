// Task 6-pre round-trip verification harness. Mirrors RevitBuilder.Build()
// (revit/RevitBuilder/RevitBuilder.cs) decision-for-decision, WITHOUT calling
// any Autodesk.Revit.DB API (none are usable outside a running Revit
// process). Every "would create element" call in the real builder becomes a
// plain record here; every condition is copied verbatim with a citation of
// the RevitBuilder.cs line it mirrors, so this harness can go stale-checked
// against the real source instead of silently drifting from it.
//
// Uses the REAL LayoutModel.cs (linked, not copied) for JSON parsing, so
// deserialization behavior (case-insensitivity, `from` alias, defaults) is
// identical to what the real builder sees.
using System.Text.Json;
using System.Text.Json.Serialization;
using BumEngine.Revit;
using BumEngine.RoundTrip;

if (args.Length != 1)
{
    Console.Error.WriteLine("usage: DryRunMapper <layout.json>");
    return 1;
}

var layout = LayoutModel.Load(args[0]);
var report = DryRunBuilder.Run(layout);

var opts = new JsonSerializerOptions
{
    WriteIndented = true,
    DefaultIgnoreCondition = JsonIgnoreCondition.Never,
};
Console.WriteLine(JsonSerializer.Serialize(report, opts));
return 0;

namespace BumEngine.RoundTrip
{
    public sealed class DryRunReport
    {
        // Top-level LayoutModel fields RevitBuilder.Build actually reads vs.
        // ignores, per a full line-by-line audit of RevitBuilder.cs (Phase 0
        // of Task 6-pre). Hardcoded here (not re-derived at runtime) because
        // it is a fact about the SOURCE CODE, not the data; kept next to the
        // dynamic report so both travel together in one artifact.
        public List<string> FieldsRead { get; } = new()
        {
            "walls[].id", "walls[].start", "walls[].end", "walls[].thickness_m",
            "rooms[].name", "rooms[].rect_m (via center)",
            "doors[].from/to (warning text only, not stored)", "doors[].wall_id",
            "doors[].center", "doors[].width_m", "doors[].height_m",
            "windows[].room (warning text only, not stored)", "windows[].wall_id",
            "windows[].center", "windows[].width_m", "windows[].height_m", "windows[].sill_m",
            "entry (folded into doors[] iteration if wall_id non-empty)",
            "wall_height_m (ONE value applied to EVERY wall)",
            "warnings (copied through to BuildResult, not re-validated)",
        };
        public List<string> FieldsIgnored { get; } = new()
        {
            "version", "preset", "seed", "objective",
            "levels (single hardcoded Level at elevation 0 always created)",
            "plot (no property line / site boundary element ever created)",
            "orientation (no rotation transform applied)",
            "rooms[].category", "rooms[].zone",
            "walls[].height_m (per-wall; superseded by the single wall_height_m)",
            "walls[].exterior (no Revit Function/Exterior-Interior param set)",
            "terrace (deserialized, never read in Build())",
        };

        public int WallsCreated { get; set; }
        public List<WallRec> Walls { get; } = new();
        public int DistinctWallTypes { get; set; }

        public int RoomsCreated { get; set; }
        public List<RoomRec> Rooms { get; } = new();

        public int DoorsPlaced { get; set; }
        public List<OpeningRec> Doors { get; } = new();

        public int WindowsPlaced { get; set; }
        public List<OpeningRec> Windows { get; } = new();

        public List<string> Warnings { get; } = new();
    }

    public sealed class WallRec
    {
        public string Id { get; set; } = "";
        public double[] Start { get; set; } = Array.Empty<double>();
        public double[] End { get; set; } = Array.Empty<double>();
        public double ThicknessM { get; set; }
        public string WallTypeName { get; set; } = "";
    }

    public sealed class RoomRec
    {
        public string Name { get; set; } = "";
        public string Number { get; set; } = "";
        public double CenterX { get; set; }
        public double CenterY { get; set; }
    }

    public sealed class OpeningRec
    {
        public string From { get; set; } = "";
        public string To { get; set; } = "";
        public string WallId { get; set; } = "";
        public double[] Center { get; set; } = Array.Empty<double>();
        public double WidthM { get; set; }
    }

    public static class DryRunBuilder
    {
        public static DryRunReport Run(LayoutModel layout)
        {
            var report = new DryRunReport();

            // ---- CreateWalls, mirrors RevitBuilder.cs:82-106 ----------------
            var byId = new Dictionary<string, WallRec>();
            var typeCache = new Dictionary<double, string>();
            foreach (var w in layout.Walls)
            {
                // RevitBuilder.cs:92 checks start.DistanceTo(end) < 1e-6 in FEET
                // post-conversion; mirrored in metres with the equivalent
                // threshold (1e-6 ft = 1e-6 / 3.280839895013123 m).
                var dx = w.End[0] - w.Start[0];
                var dy = w.End[1] - w.Start[1];
                var lenM = Math.Sqrt(dx * dx + dy * dy);
                if (lenM < 1e-6 / 3.280839895013123)
                {
                    report.Warnings.Add($"skipped zero-length wall {w.Id}");
                    continue;
                }
                // RevitBuilder.cs:110-132 GetWallType: cache keyed on the RAW
                // thickness_m double; first sighting of a thickness "creates"
                // (records) a new WallType, later exact matches reuse it.
                if (!typeCache.TryGetValue(w.ThicknessM, out var typeName))
                {
                    typeName = $"BUM {w.ThicknessM:0.###}m";
                    typeCache[w.ThicknessM] = typeName;
                }
                var rec = new WallRec
                {
                    Id = w.Id,
                    Start = w.Start,
                    End = w.End,
                    ThicknessM = w.ThicknessM,
                    WallTypeName = typeName,
                };
                byId[w.Id] = rec;
                report.Walls.Add(rec);
                report.WallsCreated++;
            }
            report.DistinctWallTypes = typeCache.Count;

            // ---- CreateRooms, mirrors RevitBuilder.cs:136-161 ---------------
            // LIMITATION: the real NewRoom(level, uv) can return null if the
            // point isn't enclosed by a real wall loop (RevitBuilder.cs:147-151).
            // That requires Revit's room-bounding solver; not simulated here.
            // Every room below is reported as if it enclosed successfully —
            // the best case, not a proof of enclosure.
            int number = 1;
            foreach (var r in layout.Rooms)
            {
                report.Rooms.Add(new RoomRec
                {
                    Name = r.Name,
                    Number = (number++).ToString(),
                    CenterX = r.CenterX,
                    CenterY = r.CenterY,
                });
                report.RoomsCreated++;
            }

            // ---- PlaceDoors, mirrors RevitBuilder.cs:165-192 ----------------
            var allDoors = new List<Door>(layout.Doors);
            if (layout.Entry != null && !string.IsNullOrEmpty(layout.Entry.WallId))
                allDoors.Add(layout.Entry);
            foreach (var d in allDoors)
            {
                if (!byId.TryGetValue(d.WallId, out _))
                {
                    report.Warnings.Add($"door {d.From}->{d.To} host wall {d.WallId} missing");
                    continue;
                }
                report.Doors.Add(new OpeningRec
                {
                    From = d.From, To = d.To, WallId = d.WallId, Center = d.Center, WidthM = d.WidthM,
                });
                report.DoorsPlaced++;
            }

            // ---- PlaceWindows, mirrors RevitBuilder.cs:194-218 ---------------
            foreach (var wd in layout.Windows)
            {
                if (!byId.TryGetValue(wd.WallId, out _))
                {
                    report.Warnings.Add($"window in {wd.Room} host wall {wd.WallId} missing");
                    continue;
                }
                report.Windows.Add(new OpeningRec
                {
                    From = wd.Room, To = "", WallId = wd.WallId, Center = wd.Center, WidthM = wd.WidthM,
                });
                report.WindowsPlaced++;
            }

            return report;
        }
    }
}
