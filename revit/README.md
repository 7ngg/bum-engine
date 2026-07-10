# Revit exporter (C# / .NET)

The Revit API is .NET and runs **only inside Revit**, so this is a separate
component from the Python service. All model-building logic lives in one shared
class, **`RevitBuilder`**, so both hosts call identical code:

```
revit/
  RevitBuilder/        BumEngine.RevitBuilder  — host-agnostic builder + layout.json POCOs
  AddIn/               BumEngine.AddIn         — desktop IExternalCommand host
  DesignAutomation/    BumEngine.DA            — APS Design Automation (headless) host + client
```

- **Target:** Revit **2025** API (.NET 8). Revit API assemblies come from the
  `Nice3point.Revit.Api.*` NuGet metapackages, so the projects restore/build
  without a local Revit install (e.g. in CI). At runtime the host's real
  `RevitAPI.dll` is loaded.

## What `RevitBuilder.Build` does
Given a `layout.json` and an open `Document`, inside one `Transaction`:
1. sets project length display units to **metres** (geometry is converted to
   Revit's internal feet via `UnitUtils`);
2. creates a **Level** at 0;
3. creates native **Walls** from each `walls[]` centerline, using a per-thickness
   single-layer `WallType` and `wall_height_m`;
4. creates **Rooms** (places a room at each rect centre, sets its Name);
5. inserts **Door**/**Window** `FamilyInstance`s hosted on the wall named by
   `wall_id`, at the given centre/size (window sill applied);
6. optionally `SaveAs` a `.rvt`.

It makes **no UI calls**, so the add-in and Design Automation invoke it
identically. Missing door/window families are reported as warnings (the DA host
loads defaults from the Revit library — see `DesignAutomation/`).

## Desktop add-in (build this first)
```bash
dotnet build revit/AddIn/AddIn.csproj -c Release
```
Copy `BumEngine.AddIn.dll`, `BumEngine.RevitBuilder.dll` and `BumEngine.addin`
into `%APPDATA%\Autodesk\Revit\Addins\2025\`. In Revit: **Add-Ins → External
Tools → bum-engine: Build layout from JSON**, pick a `layout.json`; it builds the
model and saves a sibling `.rvt`.

## Tests
- **Contract (runs anywhere, no Revit):** `LayoutModel` deserialises the shared
  `layout.json` wire format; exercised by the geometry service's schema tests and
  a linked-source round-trip.
- **Integration (requires a Revit host):** open the produced `.rvt` and assert
  wall/room/door counts. Runs headless through the Design Automation workitem
  (see `DesignAutomation/README.md`) or via RevitTestFramework locally.

## Headless deployment
See `DesignAutomation/` for the APS Design Automation AppBundle + Activity that
wraps the same `RevitBuilder` for cloud `.rvt` generation.
