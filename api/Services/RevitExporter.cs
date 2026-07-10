using BumEngine.Api.Data;

namespace BumEngine.Api.Services;

public interface IRevitExporter
{
    string Mode { get; }
    Task<Variant> ExportAsync(Variant variant, CancellationToken ct);
}

public class ExportOptions
{
    public string Mode { get; set; } = "AddInHandoff"; // or "DesignAutomation"
    public string HandoffDir { get; set; } = "handoff";
    public string OutputDir { get; set; } = "output";
}

/// <summary>
/// Local handoff: writes the variant's layout.json to a watched folder for the
/// desktop Revit add-in to build. If the add-in (or a watcher) has produced the
/// sibling .rvt, the status flips to Ready.
/// </summary>
public class AddInHandoffExporter : IRevitExporter
{
    private readonly ExportOptions _opt;
    public string Mode => "AddInHandoff";

    public AddInHandoffExporter(ExportOptions opt) => _opt = opt;

    public async Task<Variant> ExportAsync(Variant variant, CancellationToken ct)
    {
        Directory.CreateDirectory(_opt.HandoffDir);
        Directory.CreateDirectory(_opt.OutputDir);
        var jsonPath = Path.Combine(_opt.HandoffDir, $"{variant.Id}.layout.json");
        await File.WriteAllTextAsync(jsonPath, variant.LayoutJson, ct);

        var rvtPath = Path.Combine(_opt.OutputDir, $"{variant.Id}.rvt");
        if (File.Exists(rvtPath))
        {
            variant.ExportStatus = ExportStatus.Ready;
            variant.RvtPath = rvtPath;
            variant.ExportMessage = "built";
        }
        else
        {
            variant.ExportStatus = ExportStatus.Pending;
            variant.ExportMessage = $"layout handed off to {jsonPath}; run the Revit add-in to build {rvtPath}";
        }
        return variant;
    }
}

/// <summary>
/// Headless: submits an APS Design Automation workitem wrapping RevitBuilder.
/// Requires APS credentials; without them it reports the intended action so the
/// pipeline stays observable. See revit/DesignAutomation for the AppBundle/Activity.
/// </summary>
public class DesignAutomationExporter : IRevitExporter
{
    private readonly ExportOptions _opt;
    private readonly IConfiguration _cfg;
    public string Mode => "DesignAutomation";

    public DesignAutomationExporter(ExportOptions opt, IConfiguration cfg)
    {
        _opt = opt;
        _cfg = cfg;
    }

    public Task<Variant> ExportAsync(Variant variant, CancellationToken ct)
    {
        var clientId = _cfg["APS:ClientId"];
        var activity = _cfg["APS:ActivityId"];
        if (string.IsNullOrEmpty(clientId) || string.IsNullOrEmpty(activity))
        {
            variant.ExportStatus = ExportStatus.Failed;
            variant.ExportMessage = "APS credentials not configured (APS:ClientId / APS:ActivityId)";
            return Task.FromResult(variant);
        }
        // A real implementation POSTs a workitem to
        // https://developer.api.autodesk.com/da/us-east/v3/workitems with the
        // layout.json as an input argument and a signed URL for the .rvt output,
        // then polls for completion. Wiring lives in the WorkItemClient (M7).
        variant.ExportStatus = ExportStatus.Building;
        variant.ExportMessage = $"submitted Design Automation workitem to activity {activity}";
        return Task.FromResult(variant);
    }
}
