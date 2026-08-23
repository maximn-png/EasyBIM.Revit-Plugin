// LevelCodeService.cs — spec §7 (auto-suggestion) + read/write EB_Level_Code.
using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;

namespace EasyBIM.LevelSheets.Services
{
    public static class LevelCodeService
    {
        /// <summary>
        /// Spec §7 heuristic: ground = elevation closest to 0 -> "GF00"; going down
        /// "B01, B02, ..."; going up "F01, F02, ..."; topmost above-ground -> "RT".
        /// Suggestion ONLY — the stored/edited value on EB_Level_Code always wins;
        /// never parse the level's Revit Name for the code (it may be non-English /
        /// architect-linked and unreliable per spec §5).
        /// </summary>
        public static Dictionary<ElementId, string> SuggestCodes(IList<Level> levels)
        {
            var ordered = levels.OrderBy(l => l.Elevation).ToList();
            var result = new Dictionary<ElementId, string>();
            if (ordered.Count == 0) return result;

            int groundIdx = 0;
            for (int i = 1; i < ordered.Count; i++)
                if (Math.Abs(ordered[i].Elevation) < Math.Abs(ordered[groundIdx].Elevation))
                    groundIdx = i;

            result[ordered[groundIdx].Id] = "GF00";
            int n = 1;
            for (int i = groundIdx - 1; i >= 0; i--, n++)
                result[ordered[i].Id] = "B" + n.ToString("D2");
            n = 1;
            for (int i = groundIdx + 1; i < ordered.Count; i++, n++)
                result[ordered[i].Id] = "F" + n.ToString("D2");
            if (groundIdx < ordered.Count - 1)
                result[ordered[ordered.Count - 1].Id] = "RT";

            return result;
        }

        /// <summary>Read the stored EB_Level_Code value, or null if unset.</summary>
        public static string ReadCode(Level level, string paramName)
        {
            var p = level.LookupParameter(paramName);
            return (p != null && p.HasValue) ? p.AsString() : null;
        }

        /// <summary>Write the user-confirmed code back to EB_Level_Code. Call inside a Transaction.</summary>
        public static void WriteCode(Level level, string paramName, string code)
        {
            var p = level.LookupParameter(paramName);
            if (p == null)
                throw new InvalidOperationException(
                    "EB_Level_Code parameter not found/bound on Level " + level.Name +
                    " — call ParameterService.EnsureLevelCodeParam first.");
            p.Set(code);
        }
    }
}
