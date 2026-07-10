using System;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using BumEngine.Revit;
using Microsoft.Win32;

namespace BumEngine.Revit.AddIn
{
    /// <summary>
    /// Desktop host: an IExternalCommand that lets the user pick a layout.json and
    /// builds it into the active document via the shared <see cref="RevitBuilder"/>.
    /// All model-building logic lives in RevitBuilder; this class is only I/O + UI.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    [Regeneration(RegenerationOption.Manual)]
    public sealed class BuildLayoutCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            var uidoc = commandData.Application.ActiveUIDocument;
            if (uidoc == null)
            {
                message = "Open a project document first.";
                return Result.Failed;
            }
            var doc = uidoc.Document;

            var dlg = new OpenFileDialog
            {
                Title = "Select a layout.json",
                Filter = "Layout JSON (*.json)|*.json|All files (*.*)|*.*",
            };
            if (dlg.ShowDialog() != true)
                return Result.Cancelled;

            try
            {
                var layout = LayoutModel.Load(dlg.FileName);
                var savePath = System.IO.Path.ChangeExtension(dlg.FileName, ".rvt");
                var result = new RevitBuilder().Build(doc, layout, savePath);

                var summary =
                    $"Built {result.Walls} walls, {result.Rooms} rooms, " +
                    $"{result.Doors} doors, {result.Windows} windows.\n" +
                    $"Saved: {result.SavedPath}";
                if (result.Warnings.Count > 0)
                    summary += "\n\nWarnings:\n- " + string.Join("\n- ", result.Warnings);

                TaskDialog.Show("bum-engine", summary);
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                message = ex.Message;
                TaskDialog.Show("bum-engine — error", ex.ToString());
                return Result.Failed;
            }
        }
    }
}
