using System;
using System.IO;
using Autodesk.Revit.ApplicationServices;
using Autodesk.Revit.DB;
using DesignAutomationFramework;

namespace BumEngine.Revit.DA
{
    /// <summary>
    /// Headless host for APS Design Automation for Revit. Registered as the
    /// AppBundle entry point, it runs when the engine fires
    /// DesignAutomationReadyEvent, reads the input layout.json from the working
    /// directory, and builds the model with the SAME <see cref="RevitBuilder"/>
    /// the desktop add-in uses. Output is saved as result.rvt for the Activity to
    /// return.
    /// </summary>
    public sealed class DesignAutomationApp : IExternalDBApplication
    {
        private const string InputName = "layout.json";
        private const string OutputName = "result.rvt";

        public ExternalDBApplicationResult OnStartup(ControlledApplication app)
        {
            DesignAutomationBridge.DesignAutomationReadyEvent += OnReady;
            return ExternalDBApplicationResult.Succeeded;
        }

        public ExternalDBApplicationResult OnShutdown(ControlledApplication app)
            => ExternalDBApplicationResult.Succeeded;

        private void OnReady(object sender, DesignAutomationReadyEventArgs e)
        {
            e.Succeeded = Run(e.DesignAutomationData.RevitApp);
        }

        private static bool Run(Application revitApp)
        {
            var inputPath = Path.Combine(Directory.GetCurrentDirectory(), InputName);
            if (!File.Exists(inputPath))
            {
                Console.WriteLine($"[bum-engine] missing {InputName} in working dir");
                return false;
            }

            var layout = LayoutModel.Load(inputPath);

            // Start from an empty metric project template if available, else a blank doc.
            var doc = revitApp.NewProjectDocument(UnitSystem.Metric);

            var result = new RevitBuilder().Build(doc, layout, saveAsPath: null);
            Console.WriteLine(
                $"[bum-engine] built walls={result.Walls} rooms={result.Rooms} " +
                $"doors={result.Doors} windows={result.Windows}");
            foreach (var w in result.Warnings) Console.WriteLine($"[bum-engine][warn] {w}");

            var outPath = Path.Combine(Directory.GetCurrentDirectory(), OutputName);
            doc.SaveAs(outPath, new SaveAsOptions { OverwriteExistingFile = true });
            return File.Exists(outPath);
        }
    }
}
