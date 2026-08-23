// CreateLevelSheetsCommand.cs — IExternalCommand entry point.
// Opens the Level Sheets dialog and orchestrates the run per spec §9.
using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using EasyBIM.LevelSheets.Model;
using EasyBIM.LevelSheets.Services;

namespace EasyBIM.LevelSheets
{
    [Transaction(TransactionMode.Manual)]
    [Regeneration(RegenerationOption.Manual)]
    public class CreateLevelSheetsCommand : IExternalCommand
    {
        // Naming standard constants — spec §4. Keep these here, not scattered in services.
        public const string FP_TEMPLATE_NAME = "EB_MEP_FP_1-50";
        public const string CP_TEMPLATE_NAME = "EB_MEP_CP_1-50";
        public const int SHEET_NUMBER_START = 200;
        public const string LEVEL_CODE_PARAM = "EB_Level_Code";

        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIDocument uidoc = commandData.Application.ActiveUIDocument;
            Document doc = uidoc.Document;

            // 1. Preflight: resolve templates by exact name (spec §9.1).
            View fpTemplate = FindViewTemplate(doc, FP_TEMPLATE_NAME);
            View cpTemplate = FindViewTemplate(doc, CP_TEMPLATE_NAME);
            if (fpTemplate == null || cpTemplate == null)
            {
                TaskDialog.Show("Level Sheets",
                    "Missing view template(s): " +
                    (fpTemplate == null ? FP_TEMPLATE_NAME + " " : "") +
                    (cpTemplate == null ? CP_TEMPLATE_NAME : ""));
                return Result.Cancelled;
            }

            // 2. Ensure EB_Level_Code exists (spec §8) — do this before showing the dialog
            //    so the dialog can pre-fill stored codes.
            using (var t = new Transaction(doc, "EasyBIM: Ensure Level Code Parameter"))
            {
                t.Start();
                ParameterService.EnsureLevelCodeParam(doc, commandData.Application.Application);
                t.Commit();
            }

            // 3. Show the dialog (WPF — see Ui/LevelSheetsDialog.README.md for the spec).
            //    var vm = new LevelSheetsViewModel(doc, LEVEL_CODE_PARAM);
            //    var dlg = new LevelSheetsDialog(vm);
            //    if (dlg.ShowDialog() != true) return Result.Cancelled;
            //    var selection = vm.GetConfirmedSelection(); // levels + codes + pickers
            throw new NotImplementedException(
                "Build LevelSheetsDialog (WPF) per the UI spec, collect the confirmed " +
                "level/code list + title block + FP/CP ViewFamilyTypes, then call Run(...).");

            // 4. Run (spec §9.2-9.4, §10) — sketch:
            // var report = new RunReport();
            // var tg = new TransactionGroup(doc, "EasyBIM: Level Sheets");
            // tg.Start();
            // int sheetNum = SHEET_NUMBER_START;
            // foreach (var level in selection.OrderBy(l => l.Elevation))
            // {
            //     using (var t = new Transaction(doc, "EasyBIM: Level Sheet - " + level.Code))
            //     {
            //         t.Start();
            //         try
            //         {
            //             level.RevitLevel.LookupParameter(LEVEL_CODE_PARAM).Set(level.Code);
            //             var fp = ViewFactory.CreateFloorPlan(doc, level.RevitLevel, selection.FpVft, fpTemplate, level.Code);
            //             var cp = ViewFactory.CreateCeilingPlan(doc, level.RevitLevel, selection.CpVft, cpTemplate, level.Code);
            //             var sheet = SheetFactory.CreateSheet(doc, selection.TitleBlockId, ref sheetNum, level.Code);
            //             ViewportLayout.PlaceStacked(doc, sheet, cp, fp, report);
            //             report.AddSuccess(level.Code, sheet, fp, cp);
            //             t.Commit();
            //         }
            //         catch (Exception ex) { t.RollBack(); report.AddFailure(level.Code, ex.Message); }
            //     }
            // }
            // tg.Assimilate();
            // ShowRunReport(report);
            // return Result.Succeeded;
        }

        static View FindViewTemplate(Document doc, string name)
        {
            return new FilteredElementCollector(doc).OfClass(typeof(View))
                .Cast<View>()
                .FirstOrDefault(v => v.IsTemplate && v.Name == name);
        }
    }
}
