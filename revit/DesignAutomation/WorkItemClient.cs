using System;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace BumEngine.Revit.DA
{
    /// <summary>
    /// Minimal APS Design Automation v3 workitem client (pure HTTP, no Revit
    /// dependency), so the orchestrator can submit a headless build. Handles
    /// 2-legged auth, workitem submission and polling. Wire real signed URLs for
    /// the input layout.json and the result.rvt output (e.g. via OSS buckets).
    /// </summary>
    public sealed class WorkItemClient
    {
        private const string Base = "https://developer.api.autodesk.com";
        private readonly HttpClient _http;
        private readonly string _clientId;
        private readonly string _clientSecret;

        public WorkItemClient(HttpClient http, string clientId, string clientSecret)
        {
            _http = http;
            _clientId = clientId;
            _clientSecret = clientSecret;
        }

        public async Task<string> GetTokenAsync(CancellationToken ct = default)
        {
            var form = new FormUrlEncodedContent(new[]
            {
                new System.Collections.Generic.KeyValuePair<string, string>("client_id", _clientId),
                new System.Collections.Generic.KeyValuePair<string, string>("client_secret", _clientSecret),
                new System.Collections.Generic.KeyValuePair<string, string>("grant_type", "client_credentials"),
                new System.Collections.Generic.KeyValuePair<string, string>("scope", "code:all data:write data:read"),
            });
            using var resp = await _http.PostAsync($"{Base}/authentication/v2/token", form, ct);
            resp.EnsureSuccessStatusCode();
            var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));
            return doc.RootElement.GetProperty("access_token").GetString()!;
        }

        /// <summary>Submit a workitem and poll until it finishes. Returns the final status.</summary>
        public async Task<string> RunAsync(
            string token, string activityId, string layoutJsonUrl, string resultRvtUploadUrl,
            TimeSpan? timeout = null, CancellationToken ct = default)
        {
            var body = new
            {
                activityId,
                arguments = new
                {
                    layoutJson = new { url = layoutJsonUrl, verb = "get" },
                    result = new { url = resultRvtUploadUrl, verb = "put" },
                },
            };
            using var req = new HttpRequestMessage(HttpMethod.Post, $"{Base}/da/us-east/v3/workitems")
            {
                Content = JsonContent.Create(body),
            };
            req.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
            using var resp = await _http.SendAsync(req, ct);
            resp.EnsureSuccessStatusCode();
            var created = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));
            var id = created.RootElement.GetProperty("id").GetString()!;

            var deadline = DateTime.UtcNow + (timeout ?? TimeSpan.FromMinutes(10));
            while (DateTime.UtcNow < deadline)
            {
                await Task.Delay(TimeSpan.FromSeconds(3), ct);
                using var poll = new HttpRequestMessage(HttpMethod.Get, $"{Base}/da/us-east/v3/workitems/{id}");
                poll.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
                using var pr = await _http.SendAsync(poll, ct);
                var status = JsonDocument.Parse(await pr.Content.ReadAsStringAsync(ct))
                    .RootElement.GetProperty("status").GetString()!;
                if (status is not ("pending" or "inprogress"))
                    return status; // success | failed* | cancelled
            }
            return "timeout";
        }
    }
}
