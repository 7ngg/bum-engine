# Design Automation (headless `.rvt`)

Runs the **same `RevitBuilder`** as the desktop add-in, but inside the APS
Design Automation for Revit engine — no interactive Revit needed.

- `DesignAutomationApp.cs` — AppBundle entry (`IExternalDBApplication`). On
  `DesignAutomationReadyEvent` it reads `layout.json` from the working directory,
  builds the model, and saves `result.rvt`.
- `PackageContents.xml` — AppBundle descriptor (engine `Autodesk.Revit+2025`).
- `WorkItemClient.cs` — pure-HTTP APS DA v3 client (auth → submit → poll) the
  orchestrator uses in `Export:Mode=DesignAutomation`.

## Prerequisites
- An APS app (client id/secret) with the Design Automation API enabled.
- `DesignAutomationBridge.dll` from the Revit DA SDK dropped in `./libs`.

## Package & deploy
```bash
dotnet build revit/DesignAutomation/DesignAutomation.csproj -c Release

# assemble the bundle
mkdir -p BumEngineDA.bundle/Contents
cp PackageContents.xml BumEngineDA.bundle/
cp bin/Release/net8.0-windows/BumEngine.DA.dll \
   bin/Release/net8.0-windows/BumEngine.RevitBuilder.dll \
   libs/DesignAutomationBridge.dll \
   BumEngineDA.bundle/Contents/
zip -r BumEngineDA.zip BumEngineDA.bundle
```

Then, via the DA REST API (or the `Autodesk.Forge.DesignAutomation` SDK):
1. **AppBundle** — create `BumEngineDA`, upload `BumEngineDA.zip`, alias `prod`.
2. **Activity** — engine `Autodesk.Revit+2025`, one input `layoutJson`
   (localName `layout.json`) and one output `result` (localName `result.rvt`),
   command line invoking the bundle.
3. **Workitem** — supply a signed GET url for `layout.json` and a signed PUT url
   for `result.rvt`. `WorkItemClient.RunAsync` does this and polls to completion.

## Integration test (headless)
Submit a workitem for a known `layout.json`, download `result.rvt`, then open it
(RevitTestFramework or a follow-up DA activity) and assert wall/room/door counts
match the layout. This is the RevitBuilder integration test that cannot run
without a Revit engine.
