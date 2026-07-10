using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace BumEngine.Revit
{
    /// <summary>
    /// POCO mirror of layout.json (schema v1.0.0). All coordinates are metres,
    /// origin at the plot SW corner, +x east, +y north. This is the single wire
    /// contract the geometry service and the Revit builder share.
    /// </summary>
    public sealed class LayoutModel
    {
        [JsonPropertyName("version")] public string Version { get; set; } = "1.0.0";
        [JsonPropertyName("preset")] public string Preset { get; set; } = "";
        [JsonPropertyName("seed")] public int Seed { get; set; }
        [JsonPropertyName("objective")] public double Objective { get; set; }
        [JsonPropertyName("levels")] public int Levels { get; set; } = 1;
        [JsonPropertyName("wall_height_m")] public double WallHeightM { get; set; } = 2.7;
        [JsonPropertyName("plot")] public Plot Plot { get; set; } = new();
        [JsonPropertyName("orientation")] public string Orientation { get; set; } = "N";
        [JsonPropertyName("rooms")] public List<Room> Rooms { get; set; } = new();
        [JsonPropertyName("walls")] public List<Wall> Walls { get; set; } = new();
        [JsonPropertyName("doors")] public List<Door> Doors { get; set; } = new();
        [JsonPropertyName("windows")] public List<Window> Windows { get; set; } = new();
        [JsonPropertyName("entry")] public Door Entry { get; set; } = new();
        [JsonPropertyName("terrace")] public Terrace? Terrace { get; set; }
        [JsonPropertyName("warnings")] public List<string> Warnings { get; set; } = new();

        private static readonly JsonSerializerOptions Opts = new()
        {
            PropertyNameCaseInsensitive = true,
            ReadCommentHandling = JsonCommentHandling.Skip,
        };

        public static LayoutModel Load(string path)
        {
            var json = File.ReadAllText(path);
            return Parse(json);
        }

        public static LayoutModel Parse(string json)
        {
            var model = JsonSerializer.Deserialize<LayoutModel>(json, Opts)
                        ?? throw new IOException("layout.json deserialized to null");
            return model;
        }
    }

    public sealed class Plot
    {
        [JsonPropertyName("width_m")] public double WidthM { get; set; }
        [JsonPropertyName("depth_m")] public double DepthM { get; set; }
    }

    public sealed class Room
    {
        [JsonPropertyName("name")] public string Name { get; set; } = "";
        [JsonPropertyName("category")] public string Category { get; set; } = "";
        [JsonPropertyName("rect_m")] public double[] RectM { get; set; } = new double[4];
        [JsonPropertyName("zone")] public string? Zone { get; set; }

        [JsonIgnore] public double X0 => RectM[0];
        [JsonIgnore] public double Y0 => RectM[1];
        [JsonIgnore] public double X1 => RectM[2];
        [JsonIgnore] public double Y1 => RectM[3];
        [JsonIgnore] public double CenterX => (RectM[0] + RectM[2]) / 2.0;
        [JsonIgnore] public double CenterY => (RectM[1] + RectM[3]) / 2.0;
    }

    public sealed class Wall
    {
        [JsonPropertyName("id")] public string Id { get; set; } = "";
        [JsonPropertyName("start")] public double[] Start { get; set; } = new double[2];
        [JsonPropertyName("end")] public double[] End { get; set; } = new double[2];
        [JsonPropertyName("thickness_m")] public double ThicknessM { get; set; }
        [JsonPropertyName("height_m")] public double HeightM { get; set; }
        [JsonPropertyName("exterior")] public bool Exterior { get; set; }
    }

    public sealed class Door
    {
        [JsonPropertyName("from")] public string From { get; set; } = "";
        [JsonPropertyName("to")] public string To { get; set; } = "";
        [JsonPropertyName("wall_id")] public string WallId { get; set; } = "";
        [JsonPropertyName("center")] public double[] Center { get; set; } = new double[2];
        [JsonPropertyName("width_m")] public double WidthM { get; set; }
        [JsonPropertyName("height_m")] public double HeightM { get; set; }
    }

    public sealed class Window
    {
        [JsonPropertyName("room")] public string Room { get; set; } = "";
        [JsonPropertyName("wall_id")] public string WallId { get; set; } = "";
        [JsonPropertyName("center")] public double[] Center { get; set; } = new double[2];
        [JsonPropertyName("width_m")] public double WidthM { get; set; }
        [JsonPropertyName("height_m")] public double HeightM { get; set; }
        [JsonPropertyName("sill_m")] public double SillM { get; set; }
    }

    public sealed class Terrace
    {
        [JsonPropertyName("rect_m")] public double[] RectM { get; set; } = new double[4];
    }
}
