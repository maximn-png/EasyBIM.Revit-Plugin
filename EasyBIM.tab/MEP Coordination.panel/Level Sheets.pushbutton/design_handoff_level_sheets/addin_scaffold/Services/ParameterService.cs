// ParameterService.cs — spec §8: ensure EB_Level_Code exists as a text, instance
// project parameter bound to Levels (Identity Data group), created from a
// shared-parameter definition the add-in owns (stable GUID).
//
// VERSION-SENSITIVE (spec §12): SpecTypeId.String.Text (2022+) vs ParameterType.Text
// (pre-2022); GroupTypeId.IdentityData (2022+) vs BuiltInParameterGroup.PG_IDENTITY_DATA
// (older). Isolate behind #if REVIT2022_OR_GREATER (or a per-version shim) rather than
// reflection, per spec.
using System;
using System.IO;
using Autodesk.Revit.DB;

namespace EasyBIM.LevelSheets.Services
{
    public static class ParameterService
    {
        public const string PARAM_NAME = "EB_Level_Code";
        public const string SHARED_PARAM_GROUP = "EasyBIM";
        // Keep this GUID stable across releases/machines once first shipped.
        public const string PARAM_GUID = "e2a1c8d4-6b3f-4a9e-9c7d-2f5b8a1e4c6d";

        /// <summary>
        /// Idempotent: returns the existing Definition if EB_Level_Code is already
        /// bound to Levels; otherwise creates it from the add-in's shared-parameter
        /// file and binds it as an INSTANCE parameter under Identity Data.
        /// Call inside a Transaction.
        /// </summary>
        public static Definition EnsureLevelCodeParam(Document doc, Autodesk.Revit.ApplicationServices.Application app)
        {
            var it = doc.ParameterBindings.ForwardIterator();
            while (it.MoveNext())
                if (it.Key is Definition d && d.Name == PARAM_NAME) return d;

            string prevFile = app.SharedParametersFilename;
            try
            {
                string spf = GetOrCreateSharedParamFile(app);
                app.SharedParametersFilename = spf;
                DefinitionFile df = app.OpenSharedParameterFile();
                if (df == null) throw new InvalidOperationException("Could not open shared parameter file: " + spf);

                DefinitionGroup group = df.Groups.get_Item(SHARED_PARAM_GROUP) ?? df.Groups.Create(SHARED_PARAM_GROUP);
                ExternalDefinition extDef = group.Definitions.get_Item(PARAM_NAME) as ExternalDefinition;
                if (extDef == null)
                {
                    // #if REVIT2022_OR_GREATER
                    //   var opts = new ExternalDefinitionCreationOptions(PARAM_NAME, SpecTypeId.String.Text);
                    // #else
                    //   var opts = new ExternalDefinitionCreationOptions(PARAM_NAME, ParameterType.Text);
                    // #endif
                    throw new NotImplementedException(
                        "Create ExternalDefinitionCreationOptions using the version-appropriate " +
                        "spec type (see file header) — group.Definitions.Create(opts) as ExternalDefinition.");
                }

                var cats = app.Create.NewCategorySet();
                cats.Insert(Category.GetCategory(doc, BuiltInCategory.OST_Levels));
                var binding = app.Create.NewInstanceBinding(cats);

                // #if REVIT2022_OR_GREATER
                //   doc.ParameterBindings.Insert(extDef, binding, GroupTypeId.IdentityData);
                // #else
                //   doc.ParameterBindings.Insert(extDef, binding, BuiltInParameterGroup.PG_IDENTITY_DATA);
                // #endif
                throw new NotImplementedException(
                    "Insert the binding using the version-appropriate parameter group (see file header).");
            }
            finally
            {
                app.SharedParametersFilename = prevFile;
            }
        }

        static string GetOrCreateSharedParamFile(Autodesk.Revit.ApplicationServices.Application app)
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "EasyBIM", "LevelSheets");
            Directory.CreateDirectory(dir);
            string path = Path.Combine(dir, "EasyBIM_Shared.txt");
            if (!File.Exists(path)) File.WriteAllText(path, string.Empty);
            return path;
        }
    }
}
