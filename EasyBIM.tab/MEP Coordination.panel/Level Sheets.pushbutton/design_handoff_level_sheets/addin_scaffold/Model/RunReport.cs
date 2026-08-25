// RunReport.cs — accumulates per-level results/warnings for the end-of-run
// summary (spec §11): created sheet number + name, both view names, warnings.
using System.Collections.Generic;

namespace EasyBIM.LevelSheets.Model
{
    public class LevelResult
    {
        public string LevelCode;
        public string SheetNumber;
        public string SheetName;
        public string FloorPlanName;
        public string CeilingPlanName;
        public List<string> Warnings = new List<string>();
        public bool Failed;
        public string FailureReason;
    }

    public class RunReport
    {
        public List<LevelResult> Results = new List<LevelResult>();

        public void AddSuccess(string code, string sheetNumber, string sheetName, string fpName, string cpName)
        {
            Results.Add(new LevelResult
            {
                LevelCode = code, SheetNumber = sheetNumber, SheetName = sheetName,
                FloorPlanName = fpName, CeilingPlanName = cpName,
            });
        }

        public void AddWarning(string code, string warning)
        {
            var r = Results.Find(x => x.LevelCode == code);
            if (r != null) r.Warnings.Add(warning);
        }

        public void AddFailure(string code, string reason)
        {
            Results.Add(new LevelResult { LevelCode = code, Failed = true, FailureReason = reason });
        }

        public int CreatedCount => Results.FindAll(r => !r.Failed).Count;
        public int SkippedCount => Results.FindAll(r => r.Failed).Count;
    }
}
