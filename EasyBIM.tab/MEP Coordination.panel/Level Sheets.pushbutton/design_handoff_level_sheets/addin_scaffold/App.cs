// App.cs — IExternalApplication entry point. Builds the EasyBIM ribbon button
// for Level Sheets (Stage A). See reference/Level_Sheets_Stage_A_Spec.md.
using System.Reflection;
using Autodesk.Revit.UI;

namespace EasyBIM.LevelSheets
{
    public class App : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication application)
        {
            const string TAB = "EasyBIM";
            const string PANEL = "MEP Coordination";

            // Tab/panel may already exist if other EasyBIM commands are loaded first —
            // wrap CreateRibbonTab in try/catch (Revit throws if it already exists).
            try { application.CreateRibbonTab(TAB); } catch { /* already exists */ }
            RibbonPanel panel = null;
            foreach (var p in application.GetRibbonPanels(TAB))
                if (p.Name == PANEL) { panel = p; break; }
            if (panel == null) panel = application.CreateRibbonPanel(TAB, PANEL);

            string asmPath = Assembly.GetExecutingAssembly().Location;
            var data = new PushButtonData(
                "LevelSheets", "Level\nSheets", asmPath,
                typeof(CreateLevelSheetsCommand).FullName)
            {
                ToolTip = "Batch-create plan views and sheets from selected levels " +
                          "(floor plan + RCP, EasyBIM naming standard, stacked on one sheet).",
                LargeImage = System.Windows.Media.Imaging.BitmapFrame.Create(
                    new System.Uri(System.IO.Path.Combine(
                        System.IO.Path.GetDirectoryName(asmPath), "icon.png")))
            };
            panel.AddItem(data);
            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication application) => Result.Succeeded;
    }
}
