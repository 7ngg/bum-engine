using System.Text.Json;
using System.Text.Json.Nodes;
using BumEngine.Api.Data;
using BumEngine.Api.Models;
using BumEngine.Api.Services;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// --- persistence: EF Core + SQLite ---
var conn = builder.Configuration.GetConnectionString("Default") ?? "Data Source=bumengine.db";
builder.Services.AddDbContext<AppDbContext>(o => o.UseSqlite(conn));

// --- geometry service client ---
var geomUrl = builder.Configuration["GeometryService:BaseUrl"] ?? "http://localhost:8000";
builder.Services.AddHttpClient<GeometryClient>(c =>
{
    c.BaseAddress = new Uri(geomUrl);
    c.Timeout = TimeSpan.FromSeconds(120);
});

// --- Revit export strategy ---
var exportOpts = new ExportOptions();
builder.Configuration.GetSection("Export").Bind(exportOpts);
builder.Services.AddSingleton(exportOpts);
builder.Services.AddSingleton<IRevitExporter>(sp =>
    exportOpts.Mode == "DesignAutomation"
        ? new DesignAutomationExporter(exportOpts, sp.GetRequiredService<IConfiguration>())
        : new AddInHandoffExporter(exportOpts));

builder.Services.AddCors(o => o.AddDefaultPolicy(p => p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

var app = builder.Build();
app.UseCors();

using (var scope = app.Services.CreateScope())
    scope.ServiceProvider.GetRequiredService<AppDbContext>().Database.EnsureCreated();

var jsonOpts = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

app.MapGet("/health", () => Results.Ok(new { status = "ok", geometry = geomUrl, export = exportOpts.Mode }));

// web demo mode fetches this instead of keeping its own hand-maintained copy
app.MapGet("/api/example-program", async (GeometryClient geom, CancellationToken ct) =>
{
    try
    {
        using var doc = await geom.GetExampleProgramAsync(ct);
        return Results.Text(doc.RootElement.GetRawText(), "application/json");
    }
    catch (GeometryServiceException ex)
    {
        return Results.Json(new { error = ex.Message }, statusCode: 502);
    }
});

// brief -> extract -> generate -> persist program + variants
app.MapPost("/api/projects", async (CreateProjectRequest req, GeometryClient geom, AppDbContext db, CancellationToken ct) =>
{
    JsonDocument result;
    string programJson;
    try
    {
        if (req.Program is JsonElement prog)
        {
            result = await geom.GenerateAsync(prog, req.N, req.TimeLimitS, ct);
            programJson = prog.GetRawText();
        }
        else if (!string.IsNullOrWhiteSpace(req.Prompt))
        {
            result = await geom.BriefAsync(req.Prompt, req.N, ct);
            programJson = result.RootElement.GetProperty("program").GetRawText();
        }
        else
        {
            return Results.BadRequest(new { error = "provide either 'program' or 'prompt'" });
        }
    }
    catch (GeometryServiceException ex)
    {
        return Results.Json(new { error = ex.Message }, statusCode: 502);
    }

    var project = new Project { Prompt = req.Prompt, ProgramJson = programJson };
    var warnings = ReadStringArray(result.RootElement, "warnings");

    int idx = 0;
    foreach (var v in result.RootElement.GetProperty("variants").EnumerateArray())
        project.Variants.Add(ToVariant(project.Id, idx++, v));

    db.Projects.Add(project);
    await db.SaveChangesAsync(ct);

    using (result) { }
    return Results.Created($"/api/projects/{project.Id}", ToDto(project, warnings));
});

app.MapGet("/api/projects", async (AppDbContext db, CancellationToken ct) =>
    Results.Ok(await db.Projects.Include(p => p.Variants)
        .OrderByDescending(p => p.CreatedAt)
        .Select(p => new { p.Id, p.Prompt, p.CreatedAt, VariantCount = p.Variants.Count })
        .ToListAsync(ct)));

app.MapGet("/api/projects/{id}", async (string id, AppDbContext db, CancellationToken ct) =>
{
    var p = await db.Projects.Include(x => x.Variants).FirstOrDefaultAsync(x => x.Id == id, ct);
    return p is null ? Results.NotFound() : Results.Ok(ToDto(p, null));
});

app.MapGet("/api/variants/{id}/layout", async (string id, AppDbContext db, CancellationToken ct) =>
{
    var v = await db.Variants.FirstOrDefaultAsync(x => x.Id == id, ct);
    return v is null ? Results.NotFound() : Results.Content(v.LayoutJson, "application/json");
});

app.MapPost("/api/variants/{id}/export", async (string id, IRevitExporter exporter, AppDbContext db, CancellationToken ct) =>
{
    var v = await db.Variants.FirstOrDefaultAsync(x => x.Id == id, ct);
    if (v is null) return Results.NotFound();
    await exporter.ExportAsync(v, ct);
    await db.SaveChangesAsync(ct);
    return Results.Ok(new { v.Id, mode = exporter.Mode, status = v.ExportStatus.ToString(), message = v.ExportMessage });
});

app.MapGet("/api/variants/{id}/rvt", async (string id, AppDbContext db, CancellationToken ct) =>
{
    var v = await db.Variants.FirstOrDefaultAsync(x => x.Id == id, ct);
    if (v is null) return Results.NotFound();
    if (v.ExportStatus != ExportStatus.Ready || v.RvtPath is null || !File.Exists(v.RvtPath))
        return Results.Json(new { error = "rvt not ready", status = v.ExportStatus.ToString(), message = v.ExportMessage }, statusCode: 409);
    return Results.File(v.RvtPath, "application/octet-stream", $"{id}.rvt");
});

app.Run();

// --- helpers ---
static Variant ToVariant(string projectId, int index, JsonElement v)
{
    var node = JsonNode.Parse(v.GetRawText())!.AsObject();
    var svg = node.TryGetPropertyValue("svg", out var s) ? s?.GetValue<string>() ?? "" : "";
    var coverage = node.TryGetPropertyValue("coverage", out var c) ? c?.GetValue<double>() ?? 0 : 0;
    node.Remove("svg");
    node.Remove("coverage"); // keep LayoutJson schema-clean for the Revit builder
    return new Variant
    {
        ProjectId = projectId,
        Index = index,
        Preset = v.GetProperty("preset").GetString() ?? "",
        Seed = v.GetProperty("seed").GetInt32(),
        Objective = v.GetProperty("objective").GetDouble(),
        Coverage = coverage,
        Svg = svg,
        LayoutJson = node.ToJsonString(),
    };
}

static ProjectDto ToDto(Project p, IEnumerable<string>? warnings) => new(
    p.Id, p.Prompt, p.CreatedAt,
    p.Variants.OrderBy(v => v.Index).Select(v => new VariantDto(
        v.Id, v.Index, v.Preset, v.Seed, v.Objective, v.Coverage, v.Svg,
        v.ExportStatus.ToString(),
        v.ExportStatus == ExportStatus.Ready && v.RvtPath is not null && File.Exists(v.RvtPath),
        v.ExportMessage)),
    warnings);

static string[] ReadStringArray(JsonElement root, string prop) =>
    root.TryGetProperty(prop, out var a) && a.ValueKind == JsonValueKind.Array
        ? a.EnumerateArray().Select(e => e.GetString() ?? "").ToArray()
        : Array.Empty<string>();

public partial class Program { } // for WebApplicationFactory tests
