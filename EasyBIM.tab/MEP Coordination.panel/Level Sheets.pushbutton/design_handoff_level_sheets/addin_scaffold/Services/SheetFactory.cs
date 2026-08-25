// SheetFactory.cs — create sheet with unique number/name (spec §9.2 step 4, §9.4).
using System.Linq;
using Autodesk.Revit.DB;

namespace EasyBIM.LevelSheets.Services
{
    public static class SheetFactory
    {
        public static ViewSheet CreateSheet(Document doc, ElementId titleBlockId, ref int sheetNumberCounter, string code)
        {
            var sheet = ViewSheet.Create(doc, titleBlockId);
            sheet.SheetNumber = UniqueNumber(doc, sheetNumberCounter.ToString());
            sheet.Name = code + "-MEP";
            sheetNumberCounter++;
            return sheet;
        }

        /// <summary>
        /// Appends -2, -3, ... on a number clash (spec §9.4). Because numbering always
        /// restarts at 200 (spec §4), a re-run on the same model legitimately produces
        /// suffixed numbers — expected, not an error; report it (spec §11).
        /// </summary>
        public static string UniqueNumber(Document doc, string baseNumber)
        {
            var existing = new FilteredElementCollector(doc).OfClass(typeof(ViewSheet))
                .Cast<ViewSheet>().Select(s => s.SheetNumber).ToHashSet();
            if (!existing.Contains(baseNumber)) return baseNumber;
            int n = 2;
            while (existing.Contains(baseNumber + "-" + n)) n++;
            return baseNumber + "-" + n;
        }
    }
}
