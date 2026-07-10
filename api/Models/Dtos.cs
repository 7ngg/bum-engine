using System.Text.Json;

namespace BumEngine.Api.Models;

public record CreateProjectRequest(
    string? Prompt,
    JsonElement? Program,
    int N = 3,
    double TimeLimitS = 12.0);

public record VariantDto(
    string Id,
    int Index,
    string Preset,
    int Seed,
    double Objective,
    double Coverage,
    string Svg,
    string ExportStatus,
    bool RvtAvailable,
    string? ExportMessage);

public record ProjectDto(
    string Id,
    string? Prompt,
    DateTime CreatedAt,
    IEnumerable<VariantDto> Variants,
    IEnumerable<string>? Warnings = null);
