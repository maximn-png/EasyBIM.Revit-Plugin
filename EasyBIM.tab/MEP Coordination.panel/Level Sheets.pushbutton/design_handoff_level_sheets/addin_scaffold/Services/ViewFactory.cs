// ViewFactory.cs — create + template + rename FP/RCP views (spec §9.2 steps 2-3).
using System;
using System.Linq;
using Autodesk.Revit.DB;

namespace EasyBIM.LevelSheets.Services
{
    public static class ViewFactory
    {
        public static ViewPlan CreateFloorPlan(Document doc, Level level, ElementId floorPlanVftId, View template, string code)
        {
            var view = ViewPlan.Create(doc, floorPlanVftId, level.Id);
            view.ViewTemplateId = template.Id;
            view.Name = UniqueName(doc, "CD_Floor Plan_" + code);
            return view;
        }

        public static ViewPlan CreateCeilingPlan(Document doc, Level level, ElementId ceilingVftId, View template, string code)
        {
            var view = ViewPlan.Create(doc, ceilingVftId, level.Id);
            view.ViewTemplateId = template.Id;
            view.Name = UniqueName(doc, "CD_Ceiling Plan_" + code);
            return view;
        }

        /// <summary>Appends _2, _3, ... on a name clash (spec §9.4). View names are unique per document.</summary>
        public static string UniqueName(Document doc, string baseName)
        {
            var existing = new FilteredElementCollector(doc).OfClass(typeof(View))
                .Cast<View>().Select(v => v.Name).ToHashSet();
            if (!existing.Contains(baseName)) return baseName;
            int n = 2;
            while (existing.Contains(baseName + "_" + n)) n++;
            return baseName + "_" + n;
        }
    }
}
