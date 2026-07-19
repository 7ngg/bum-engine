using System.Net.Http.Json;
using System.Text.Json;

namespace BumEngine.Api.Services;

/// <summary>Typed client for the Python geometry service.</summary>
public class GeometryClient
{
    private readonly HttpClient _http;

    public GeometryClient(HttpClient http) => _http = http;

    /// <summary>program.json -> {variants[], warnings} (all validator-gated).</summary>
    public async Task<JsonDocument> GenerateAsync(JsonElement program, int n, double timeLimitS, CancellationToken ct)
    {
        var body = new { program, n, time_limit_s = timeLimitS };
        using var resp = await _http.PostAsJsonAsync("/generate", body, ct);
        return await ReadOrThrow(resp, ct);
    }

    /// <summary>natural-language prompt -> {program, variants[]} (needs Gemini key on the service).</summary>
    public async Task<JsonDocument> BriefAsync(string prompt, int n, CancellationToken ct)
    {
        var body = new { prompt, n };
        using var resp = await _http.PostAsJsonAsync("/brief", body, ct);
        return await ReadOrThrow(resp, ct);
    }

    /// <summary>The demo-mode example program (services/geometry/data/program.example.json),
    /// served by the geometry service so it stays the single source of truth.</summary>
    public async Task<JsonDocument> GetExampleProgramAsync(CancellationToken ct)
    {
        using var resp = await _http.GetAsync("/example", ct);
        return await ReadOrThrow(resp, ct);
    }

    private static async Task<JsonDocument> ReadOrThrow(HttpResponseMessage resp, CancellationToken ct)
    {
        var stream = await resp.Content.ReadAsStreamAsync(ct);
        var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
        if (!resp.IsSuccessStatusCode)
            throw new GeometryServiceException((int)resp.StatusCode, doc.RootElement.ToString());
        return doc;
    }
}

public class GeometryServiceException : Exception
{
    public int StatusCode { get; }
    public GeometryServiceException(int status, string detail)
        : base($"geometry service {status}: {detail}") => StatusCode = status;
}
