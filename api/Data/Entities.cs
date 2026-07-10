using System;
using System.Collections.Generic;

namespace BumEngine.Api.Data;

public enum ExportStatus
{
    None = 0,
    Pending = 1,   // handed off to a Revit host (add-in) / queued
    Building = 2,  // Design Automation workitem running
    Ready = 3,
    Failed = 4,
}

public class Project
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string? Prompt { get; set; }
    public string ProgramJson { get; set; } = "{}";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public List<Variant> Variants { get; set; } = new();
}

public class Variant
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string ProjectId { get; set; } = "";
    public Project? Project { get; set; }

    public int Index { get; set; }
    public string Preset { get; set; } = "";
    public int Seed { get; set; }
    public double Objective { get; set; }
    public double Coverage { get; set; }

    public string LayoutJson { get; set; } = "{}";
    public string Svg { get; set; } = "";

    public ExportStatus ExportStatus { get; set; } = ExportStatus.None;
    public string? RvtPath { get; set; }
    public string? ExportMessage { get; set; }
}
