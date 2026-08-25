// ViewportLayout.cs — spec §9.3: stacked (RCP above FP), horizontally aligned to
// the model, using each view's crop-box world-X center + the shared scale.
// Requires doc.Regenerate() after Viewport.Create before reading GetBoxOutline().
using Autodesk.Revit.DB;
using EasyBIM.LevelSheets.Model;

namespace EasyBIM.LevelSheets.Services
{
    public static class ViewportLayout
    {
        /// <summary>
        /// Places rcpTop above fpBottom on sheet, centered as a group in `usable`
        /// sheet-space, with fpBottom's model X aligned under rcpTop's model X so
        /// vertical grids run continuously between the two views.
        /// Recommended: before calling this, copy fpBottom's CropBox onto rcpTop
        /// (or set both to a common rectangle) so dU below is exactly 0 — the
        /// simplest robust alignment (spec §9.3).
        /// If the combined height exceeds `usable`'s height, KEEP the scale and
        /// add a warning to `report` — never auto-rescale (spec §5, §9.3).
        /// </summary>
        public static void PlaceStacked(Document doc, ViewSheet sheet, ViewPlan rcpTop, ViewPlan fpBottom,
            BoundingBoxUV usable, double gapFt, RunReport report, string levelCode)
        {
            var vpTop = Viewport.Create(doc, sheet.Id, rcpTop.Id, XYZ.Zero);
            var vpBot = Viewport.Create(doc, sheet.Id, fpBottom.Id, XYZ.Zero);
            doc.Regenerate(); // required before GetBoxOutline()

            Outline oT = vpTop.GetBoxOutline();
            Outline oB = vpBot.GetBoxOutline();
            double hT = oT.MaximumPoint.Y - oT.MinimumPoint.Y;
            double hB = oB.MaximumPoint.Y - oB.MinimumPoint.Y;

            double cU = (usable.Min.U + usable.Max.U) / 2.0;
            double cV = (usable.Min.V + usable.Max.V) / 2.0;

            double vTop = cV + gapFt / 2.0 + hT / 2.0;
            double vBot = cV - gapFt / 2.0 - hB / 2.0;

            double s = rcpTop.Scale; // == fpBottom.Scale (both templates are 1:50)
            double dU = (CropCenterWorldX(rcpTop) - CropCenterWorldX(fpBottom)) / s;
            double uTop = cU + dU / 2.0;
            double uBot = cU - dU / 2.0; // both == cU when crops match

            vpTop.SetBoxCenter(new XYZ(uTop, vTop, 0));
            vpBot.SetBoxCenter(new XYZ(uBot, vBot, 0));

            double usableHeight = usable.Max.V - usable.Min.V;
            if (hT + gapFt + hB > usableHeight)
                report.AddWarning(levelCode, "did not fit at 1:" + s + " within the title block — scale preserved");
        }

        static double CropCenterWorldX(View v)
        {
            BoundingBoxXYZ cb = v.CropBox;
            XYZ midView = (cb.Min + cb.Max) * 0.5;
            return cb.Transform.OfPoint(midView).X;
        }

        /// <summary>
        /// Prefer the placed title block's own sheet-space extents (inset by a margin);
        /// fall back to ViewSheet.Outline if unavailable (spec §9.3 "Usable area").
        /// </summary>
        public static BoundingBoxUV GetUsableArea(Document doc, ViewSheet sheet, double marginFt)
        {
            throw new System.NotImplementedException(
                "Collect the OST_TitleBlocks FamilyInstance with OwnerViewId == sheet.Id, " +
                "take its get_BoundingBox(sheet), inset by marginFt; else use sheet.Outline.");
        }
    }
}
