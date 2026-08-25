# -*- coding: utf-8 -*-
"""Auto Assign Level — EasyBIM DBA.

4-step wizard: pick active levels, choose a processing scope, choose which
categories to update, then review and run. Elements are reassigned to the
highest active level at or below their real Z coordinate (so a ceiling
fixture near Z=2.80m gets linked to the floor level at Z=0.00m instead of
the level above it) without ever moving the element in 3D space. How that
is guaranteed depends on the element (see classify_element/run_assignment):
hosted/face-based instances (lighting fixtures, switches, etc.) only ever
get their Level parameter changed, since their position is driven by the
host, not by Level+Offset; MEP curves and freestanding elements also get
their offset parameter recalculated so the absolute Z they were already
modeled at is preserved under the new reference level. Wrapped in a single
TransactionGroup + Assimilate() (one Undo step).

Shift-click bypasses the wizard entirely and runs on the current selection
against the last-remembered (or auto-detected) non-auxiliary levels.
"""

__title__ = "Auto Assign\nLevel"
__author__ = "EasyBIM"
__doc__ = "Assign elements to their correct floor level without moving them."

import clr
import re
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System')

from Autodesk.Revit import DB
import System
import System.Windows
import System.Windows.Controls as WC
import System.Windows.Media as WM
import System.Windows.Input as WI

from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import revit, script, forms

doc = revit.doc
logger = script.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_SECTION = "AutoAssignLevelTool"
FT_TO_M = 0.3048

AUXILIARY_KEYWORDS = [r"parapet", r"top", r"landing", r"grid", r"ref", r"soffit"]

SCOPE_SELECTION = u"selection"
SCOPE_VIEW = u"view"
SCOPE_MODEL = u"model"

# Element handling is split into three tiers so nothing ever shifts
# physically: hosted/face-based instances never get an offset write (their
# position is driven by the host, not by Level+Offset); MEP curves and
# freestanding elements get their offset recalculated so the absolute Z
# they were already modeled at is preserved after the Level change.
TIER_HOSTED = u"hosted"
TIER_MEP_CURVE = u"mep_curve"
TIER_FREESTANDING = u"freestanding"

LEVEL_PARAMS = [
    DB.BuiltInParameter.WALL_BASE_CONSTRAINT,
    DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,
    DB.BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM,
    DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM,
    DB.BuiltInParameter.FAMILY_LEVEL_PARAM,
    DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
]
MEP_LEVEL_PARAMS = [
    DB.BuiltInParameter.RBS_START_LEVEL_PARAM,
    DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM,
]
MEP_OFFSET_PARAMS = [
    DB.BuiltInParameter.RBS_OFFSET_PARAM,
]
FREESTANDING_OFFSET_PARAMS = [
    DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
    DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM,
    DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM,
]

NAVY_COLOR = WM.Color.FromRgb(0x1e, 0x24, 0x8c)
CYAN_COLOR = WM.Color.FromRgb(0x44, 0xb8, 0xd3)
GRAY_COLOR = WM.Color.FromRgb(0xc6, 0xcb, 0xe0)
GREEN_COLOR = WM.Color.FromRgb(0x22, 0xb0, 0x7c)
BODY_COLOR = WM.Color.FromRgb(0x37, 0x41, 0x51)
MUTED_COLOR = WM.Color.FromRgb(0x9a, 0xa0, 0xac)
LINE_COLOR = WM.Color.FromRgb(0xf0, 0xf1, 0xff)
SEL_BG = WM.Color.FromArgb(0x16, 0x44, 0xb8, 0xd3)
AMBER_BG = WM.Color.FromRgb(0xff, 0xf8, 0xec)
AMBER_BORDER = WM.Color.FromRgb(0xf9, 0xc9, 0x5a)
AMBER_TEXT = WM.Color.FromRgb(0x7a, 0x51, 0x00)


def _color(c):
    return WM.SolidColorBrush(c)


# ─────────────────────────────────────────────────────────────────────────────
# DATA GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def get_all_levels():
    levels = DB.FilteredElementCollector(doc) \
        .OfClass(DB.Level) \
        .WhereElementIsNotElementType() \
        .ToElements()
    return sorted(list(levels), key=lambda l: l.Elevation)


def is_auxiliary_level(name):
    return any(re.search(pat, name, re.IGNORECASE) for pat in AUXILIARY_KEYWORDS)


def format_elevation(level):
    return u"{:.2f} m".format(level.Elevation * FT_TO_M)


def get_default_active_levels(all_levels):
    """Levels checked by default: remembered exclusions, else non-auxiliary.

    cfg.get_option(name, None) is not a safe "not set yet" check here —
    pyRevit's PyRevitConfigSectionParser.get_option() only returns the given
    default when it is not None, and re-raises otherwise — so a genuine
    "never saved" case with default=None would crash instead of falling
    through. Check membership on the section directly instead.
    """
    cfg = script.get_config(CONFIG_SECTION)
    if "ignored_levels" in cfg:
        saved_ignored = cfg.ignored_levels
        return set(l.Name for l in all_levels if l.Name not in saved_ignored)
    return set(l.Name for l in all_levels if not is_auxiliary_level(l.Name))


def save_ignored_levels(all_levels, active_names):
    cfg = script.get_config(CONFIG_SECTION)
    cfg.ignored_levels = [l.Name for l in all_levels if l.Name not in active_names]
    script.save_config()


def get_scope_elements(scope):
    if scope == SCOPE_SELECTION:
        raw = list(revit.get_selection())
    elif scope == SCOPE_VIEW:
        raw = list(DB.FilteredElementCollector(doc, doc.ActiveView.Id)
                    .WhereElementIsNotElementType().ToElements())
    else:
        raw = list(DB.FilteredElementCollector(doc)
                    .WhereElementIsNotElementType().ToElements())
    return [e for e in raw if e.Category and e.Category.CategoryType == DB.CategoryType.Model]


def group_by_category(elements):
    by_cat = {}
    for e in elements:
        by_cat.setdefault(e.Category.Name, []).append(e)
    return by_cat


# ─────────────────────────────────────────────────────────────────────────────
# CORE OPERATION
# ─────────────────────────────────────────────────────────────────────────────

def get_element_z(elem):
    """Returns (z, is_sloped).

    z is the reference height used to pick a target level (the lower
    endpoint for a curve-based element). is_sloped flags a LocationCurve
    whose endpoints differ enough that a single Offset value would not
    correctly represent both ends — writing one anyway risks physically
    shifting whichever end isn't at the reference height.
    """
    if hasattr(elem, "Location") and elem.Location:
        if isinstance(elem.Location, DB.LocationPoint):
            return elem.Location.Point.Z, False
        elif isinstance(elem.Location, DB.LocationCurve):
            p0 = elem.Location.Curve.GetEndPoint(0).Z
            p1 = elem.Location.Curve.GetEndPoint(1).Z
            return min(p0, p1), abs(p0 - p1) > 0.01
    bbox = elem.get_BoundingBox(None)
    return (bbox.Min.Z, False) if bbox else (None, False)


def find_associated_level(active_levels, z_coord):
    """Highest active level at or below z_coord (+0.01 tolerance)."""
    selected_level = active_levels[0]
    for lvl in active_levels:
        if lvl.Elevation <= z_coord + 0.01:
            selected_level = lvl
        else:
            break
    return selected_level


def _first_param(elem, bips, storage_type):
    """First candidate with the expected StorageType, preferring a
    writable one; else the first that exists with that StorageType even
    if locked (so callers can still detect/report a genuinely locked
    parameter), else None.

    A family can expose more than one plausible parameter from a
    BuiltInParameter list (e.g. both "Elevation from Level" and
    "Offset from Host" on the same unhosted instance) — stopping at
    whichever one happens to exist first, regardless of whether it is
    read-only, can silently pick the wrong one and leave the real,
    position-driving parameter untouched. The StorageType check also
    guards against a same-named/same-BuiltInParameter field that isn't
    actually the numeric/level field expected here (a mismatch would
    otherwise throw when .Set() is called with the wrong value type).
    """
    fallback = None
    for bip in bips:
        p = elem.get_Parameter(bip)
        if p and p.StorageType == storage_type:
            if fallback is None:
                fallback = p
            if not p.IsReadOnly:
                return p
    return fallback


def _first_named_or_param(elem, names, bips, storage_type):
    """Prefer a parameter found by its exact on-screen name (matching
    whatever the user sees in the Properties palette, e.g. "Elevation
    from Level"), falling back to the BuiltInParameter candidate list
    when no named match is writable. StorageType is checked either way,
    since Element.LookupParameter can return an arbitrary match if more
    than one parameter happens to share that display name."""
    for name in names:
        p = elem.LookupParameter(name)
        if p and p.StorageType == storage_type and not p.IsReadOnly:
            return p
    return _first_param(elem, bips, storage_type)


def classify_element(elem):
    """Which offset-handling tier this element falls into.

    MEP curves (ducts/pipes/cable trays/conduits) and hosted/face-based
    family instances (lighting fixtures, switches, air terminals, etc.)
    are checked first since isinstance/Host/HostFace are unambiguous;
    everything else is treated as a freestanding point/line element.
    """
    if isinstance(elem, DB.MEPCurve):
        return TIER_MEP_CURVE

    if isinstance(elem, DB.FamilyInstance):
        host = None
        host_face = None
        try:
            host = elem.Host
        except Exception:
            pass
        try:
            host_face = elem.HostFace
        except Exception:
            pass
        if host is not None or host_face is not None:
            return TIER_HOSTED

    return TIER_FREESTANDING


def run_assignment(elements, active_levels, process_groups):
    """Runs the whole operation as one undoable step. Returns a result dict.

    Never moves an element physically: hosted/face-based instances only
    ever get their Level parameter changed (see classify_element); MEP
    curves and freestanding elements also get their offset recalculated so
    the absolute Z they were already modeled at is preserved. Nothing is
    highlighted in the view either — updated elements are reported via the
    result panel and the console report's clickable Element Id links
    instead, so the view is left completely unchanged after the run.
    """
    tg = DB.TransactionGroup(doc, u"Auto Assign Level")
    tg.Start()
    updated_stats = {}
    updated_ids = []
    skipped_groups = 0
    skipped_workshare = 0
    skipped_hosted = 0
    skipped_sloped = 0
    skipped_errors = 0

    try:
        t = DB.Transaction(doc, u"Assign levels")
        t.Start()

        for elem in elements:
            try:
                if elem.GroupId != DB.ElementId.InvalidElementId and not process_groups:
                    skipped_groups += 1
                    continue

                if doc.IsWorkshared and DB.WorksharingUtils.GetCheckoutStatus(
                        doc, elem.Id) == DB.CheckoutStatus.OwnedByOtherUser:
                    skipped_workshare += 1
                    continue

                z_pos, is_sloped = get_element_z(elem)
                if z_pos is None:
                    continue

                target_level = find_associated_level(active_levels, z_pos)
                tier = classify_element(elem)

                # A single Offset value can't correctly represent both ends
                # of a sloped run (e.g. a drainage pipe) — writing one
                # anyway would use the lower endpoint's height for the
                # whole element and risk shifting whichever end isn't
                # there. Leave these untouched rather than guess.
                if is_sloped and tier in (TIER_MEP_CURVE, TIER_FREESTANDING):
                    skipped_sloped += 1
                    continue

                level_param = _first_param(
                    elem, MEP_LEVEL_PARAMS if tier == TIER_MEP_CURVE else LEVEL_PARAMS,
                    DB.StorageType.ElementId)

                # Hosted/face-based instances only drive physical position
                # via their host — that does not make the Level parameter
                # itself uneditable, so they still get their level fixed
                # (just never an offset write, below). Only elements whose
                # level parameter is genuinely missing or locked are
                # skipped here.
                if level_param is None or level_param.IsReadOnly:
                    if tier == TIER_HOSTED:
                        skipped_hosted += 1
                    continue

                if level_param.AsElementId() == target_level.Id:
                    continue

                level_param.Set(target_level.Id)

                if tier == TIER_MEP_CURVE:
                    # Ducts/pipes/cable trays/conduits: recalculate the
                    # offset so the absolute Z height is preserved under
                    # the new reference level. Prefer the exact on-screen
                    # "Offset" field over guessing a BuiltInParameter,
                    # since a curve can expose more than one offset-shaped
                    # parameter.
                    offset_param = _first_named_or_param(
                        elem, [u"Offset"], MEP_OFFSET_PARAMS, DB.StorageType.Double)
                    if offset_param and not offset_param.IsReadOnly:
                        offset_param.Set(z_pos - target_level.Elevation)
                elif tier == TIER_FREESTANDING:
                    # Freestanding furniture/equipment/generic models: same
                    # offset recalculation. Prefer the exact on-screen
                    # "Elevation from Level" field — an unhosted instance
                    # can still carry an unrelated, separately-existing
                    # "Offset from Host" parameter, and picking that one
                    # instead would leave the real Z-driving parameter
                    # untouched.
                    offset_param = _first_named_or_param(
                        elem, [u"Elevation from Level"], FREESTANDING_OFFSET_PARAMS,
                        DB.StorageType.Double)
                    if offset_param and not offset_param.IsReadOnly:
                        offset_param.Set(z_pos - target_level.Elevation)
                # TIER_HOSTED: level only — never touch offset, so the
                # element stays flush against its host wall/ceiling face.

                cat_name = elem.Category.Name
                updated_stats[cat_name] = updated_stats.get(cat_name, 0) + 1
                updated_ids.append(elem.Id)
            except Exception as ex:
                # One anomalous element (an unexpected parameter type, a
                # transient API error, etc.) should not roll back every
                # other change already computed in this run.
                skipped_errors += 1
                logger.warning(u"Auto Assign Level: skipped element {} — {}".format(elem.Id, ex))
                continue

        t.Commit()
        tg.Assimilate()
        return {
            u"updated_stats": updated_stats,
            u"updated_ids": updated_ids,
            u"skipped_groups": skipped_groups,
            u"skipped_workshare": skipped_workshare,
            u"skipped_hosted": skipped_hosted,
            u"skipped_sloped": skipped_sloped,
            u"skipped_errors": skipped_errors,
        }
    except Exception:
        tg.RollBack()
        raise


def print_console_report(result):
    output = script.get_output()
    output.print_md(u"# Auto Assign Level — Report")

    updated_stats = result[u"updated_stats"]
    updated_ids = result[u"updated_ids"]
    total = sum(updated_stats.values())

    if total:
        output.print_md(u"### Elements updated")
        for cat, count in sorted(updated_stats.items()):
            print(u"- {}: {}".format(cat, count))
        output.print_md(u"\n**Total updated:** `{}`".format(total))

        print(u"\nUpdated Element IDs (click to select in model):")
        for eid in updated_ids[:30]:
            print(output.linkify(eid))
        if len(updated_ids) > 30:
            print(u"... and {} more.".format(len(updated_ids) - 30))
    else:
        output.print_md(u"All checked elements were already on the correct level.")

    if result[u"skipped_groups"]:
        output.print_md(u"Skipped **{}** element(s) inside Model Groups.".format(result[u"skipped_groups"]))
    if result[u"skipped_workshare"]:
        output.print_md(u"Skipped **{}** element(s) checked out by another user.".format(result[u"skipped_workshare"]))
    if result[u"skipped_hosted"]:
        output.print_md(u"Skipped **{}** element(s) whose level is locked to their host (e.g. doors/windows).".format(result[u"skipped_hosted"]))
    if result[u"skipped_sloped"]:
        output.print_md(u"Skipped **{}** sloped run(s) (e.g. drainage pipes) — a single Offset value can't represent both ends safely.".format(result[u"skipped_sloped"]))
    if result[u"skipped_errors"]:
        output.print_md(u"Skipped **{}** element(s) due to an unexpected error — see the pyRevit log for details.".format(result[u"skipped_errors"]))


# ─────────────────────────────────────────────────────────────────────────────
# QUICK RUN (SHIFT-CLICK)
# ─────────────────────────────────────────────────────────────────────────────

def quick_run():
    all_levels = get_all_levels()
    if not all_levels:
        forms.alert(u"No levels found in this project.", title=u"Auto Assign Level", exitscript=True)

    active_names = get_default_active_levels(all_levels)
    active_levels = sorted([l for l in all_levels if l.Name in active_names], key=lambda l: l.Elevation)
    if not active_levels:
        forms.alert(
            u"No active levels are configured. Run the tool once without "
            u"Shift to choose which levels to use.",
            title=u"Auto Assign Level", exitscript=True)

    selection = list(revit.get_selection())
    if not selection:
        forms.alert(u"Quick run (Shift-click): select elements first.", title=u"Auto Assign Level", exitscript=True)

    valid = [e for e in selection if e.Category and e.Category.CategoryType == DB.CategoryType.Model]
    if not valid:
        forms.alert(u"No model elements found in the current selection.", title=u"Auto Assign Level", exitscript=True)

    try:
        result = run_assignment(valid, active_levels, process_groups=True)
    except Exception:
        forms.alert(u"Auto Assign Level failed, all changes rolled back:\n\n{}".format(traceback.format_exc()),
                    title=u"Auto Assign Level — Error")
        return

    print_console_report(result)
    total = sum(result[u"updated_stats"].values())
    forms.alert(
        u"{} element(s) updated.".format(total) if total else u"All selected elements were already on the correct level.",
        title=u"Auto Assign Level")


# ─────────────────────────────────────────────────────────────────────────────
# WPF XAML
# ─────────────────────────────────────────────────────────────────────────────

XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Auto Assign Level"
  Width="624" Height="720"
  WindowStartupLocation="CenterScreen"
  ResizeMode="CanMinimize"
  WindowStyle="SingleBorderWindow"
  FontFamily="Segoe UI"
  Background="#f7f8ff">
  <Window.Resources>

    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background"      Value="#1e248c"/>
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="18,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" CornerRadius="7"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#44b8d3"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter Property="Opacity" Value="0.42"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#6b7280"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="14,0"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush"     Value="#e8eaff"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="7" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#f0f2ff"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CyanTextBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#44b8d3"/>
      <Setter Property="FontSize"        Value="12"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Height"          Value="22"/>
      <Setter Property="Padding"         Value="0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
    </Style>

  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="76"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- HEADER -->
    <Border Grid.Row="0">
      <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
          <GradientStop Color="#1e248c" Offset="0"/>
          <GradientStop Color="#2b5cbf" Offset="0.55"/>
          <GradientStop Color="#44b8d3" Offset="1"/>
        </LinearGradientBrush>
      </Border.Background>
      <Grid Margin="20,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="32"/>
        </Grid.ColumnDefinitions>
        <Border Grid.Column="0" Width="44" Height="44" CornerRadius="10" VerticalAlignment="Center"
                Background="#151b6e">
          <Path Data="M12,4 V15 M8,11 L12,16 L16,11 M4,20 H20"
                Stroke="White" StrokeThickness="1.6" StrokeLineJoin="Round"
                StrokeStartLineCap="Round" StrokeEndLineCap="Round"
                Width="24" Height="24" Stretch="None"
                HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="Auto Assign Level" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="DBA · move elements to their correct floor level"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Content="✕" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- BANNER -->
    <Border x:Name="BannerBorder" Grid.Row="1"
            Background="#ecf8fc" BorderBrush="#bbe5f0" BorderThickness="0,0,0,1" Padding="20,10">
      <StackPanel Orientation="Horizontal">
        <TextBlock Text="i" FontSize="13" FontWeight="Bold" Foreground="#44b8d3"
                   Margin="0,0,9,0" VerticalAlignment="Center" FontFamily="Segoe UI"/>
        <TextBlock x:Name="BannerText" FontSize="12.5" Foreground="#1e6e87"
                   TextWrapping="Wrap" VerticalAlignment="Center"/>
      </StackPanel>
    </Border>

    <!-- STEPPER -->
    <Grid Grid.Row="2" Margin="24,14,24,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <Border Grid.Column="0" Grid.ColumnSpan="4" Height="1.5" Background="#e0e3f8"
              VerticalAlignment="Top" Margin="38,13,38,0"/>
      <StackPanel Grid.Column="0" HorizontalAlignment="Center">
        <Border x:Name="SC1" Width="28" Height="28" CornerRadius="14" Background="#1e248c" HorizontalAlignment="Center">
          <TextBlock x:Name="SN1" Text="1" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL1" Text="Levels" FontSize="10" TextAlignment="Center"
                   Foreground="#1e248c" FontWeight="SemiBold" Margin="0,5,0,0"/>
      </StackPanel>
      <StackPanel Grid.Column="1" HorizontalAlignment="Center">
        <Border x:Name="SC2" Width="28" Height="28" CornerRadius="14" Background="#c6cbe0" HorizontalAlignment="Center">
          <TextBlock x:Name="SN2" Text="2" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL2" Text="Scope" FontSize="10" TextAlignment="Center"
                   Foreground="#9aa0ac" Margin="0,5,0,0"/>
      </StackPanel>
      <StackPanel Grid.Column="2" HorizontalAlignment="Center">
        <Border x:Name="SC3" Width="28" Height="28" CornerRadius="14" Background="#c6cbe0" HorizontalAlignment="Center">
          <TextBlock x:Name="SN3" Text="3" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL3" Text="Categories" FontSize="10" TextAlignment="Center"
                   Foreground="#9aa0ac" Margin="0,5,0,0"/>
      </StackPanel>
      <StackPanel Grid.Column="3" HorizontalAlignment="Center">
        <Border x:Name="SC4" Width="28" Height="28" CornerRadius="14" Background="#c6cbe0" HorizontalAlignment="Center">
          <TextBlock x:Name="SN4" Text="4" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL4" Text="Review" FontSize="10" TextAlignment="Center"
                   Foreground="#9aa0ac" Margin="0,5,0,0"/>
      </StackPanel>
    </Grid>

    <!-- BODY -->
    <ScrollViewer Grid.Row="3" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
      <Grid Margin="20,6,20,10">

        <!-- STEP 1: LEVELS -->
        <StackPanel x:Name="Step1Panel">
          <Grid Margin="0,0,0,9">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="LevelsLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                       Foreground="#9aa0ac" VerticalAlignment="Center"/>
            <Button x:Name="ToggleAllLevelsBtn" Grid.Column="1" Content="Select all"
                    Style="{StaticResource CyanTextBtn}"/>
          </Grid>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8">
            <ScrollViewer VerticalScrollBarVisibility="Auto" MaxHeight="330">
              <StackPanel x:Name="LevelListPanel"/>
            </ScrollViewer>
          </Border>
          <StackPanel Orientation="Horizontal" Margin="2,10,0,0">
            <TextBlock Text="i" FontSize="12" FontWeight="Bold" Foreground="#44b8d3"
                       Margin="0,0,7,0" VerticalAlignment="Center"/>
            <TextBlock Text="Only checked levels are used as assignment targets. Uncheck reference, parapet or grid levels that aren't real floors — this is remembered for next time."
                       FontSize="11.5" Foreground="#9aa0ac" TextWrapping="Wrap"/>
          </StackPanel>
        </StackPanel>

        <!-- STEP 2: SCOPE -->
        <StackPanel x:Name="Step2Panel" Visibility="Collapsed">
          <TextBlock Text="PROCESSING SCOPE" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,7"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8">
            <StackPanel x:Name="ScopeListPanel"/>
          </Border>
          <Border x:Name="ScopeWarningBorder" Background="#fff8ec" BorderBrush="#f9c95a" BorderThickness="1"
                  CornerRadius="7" Padding="12,9" Margin="0,10,0,0" Visibility="Collapsed">
            <StackPanel Orientation="Horizontal">
              <TextBlock Text="&#x26A0;" FontSize="13" Foreground="#c8850d" Margin="0,0,8,0" VerticalAlignment="Center"/>
              <TextBlock x:Name="ScopeWarningTB" FontSize="12" Foreground="#7a5100" TextWrapping="Wrap"/>
            </StackPanel>
          </Border>
        </StackPanel>

        <!-- STEP 3: CATEGORIES -->
        <StackPanel x:Name="Step3Panel" Visibility="Collapsed">
          <Grid Margin="0,0,0,9">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="CategoriesLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                       Foreground="#9aa0ac" VerticalAlignment="Center"/>
            <Button x:Name="ToggleAllCatBtn" Grid.Column="1" Content="Select all"
                    Style="{StaticResource CyanTextBtn}"/>
          </Grid>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8">
            <ScrollViewer VerticalScrollBarVisibility="Auto" MaxHeight="330">
              <StackPanel x:Name="CategoryListPanel"/>
            </ScrollViewer>
          </Border>
        </StackPanel>

        <!-- STEP 4: REVIEW + RESULT -->
        <StackPanel x:Name="Step4Panel" Visibility="Collapsed">

          <StackPanel x:Name="ReviewPanel">
            <TextBlock FontSize="13" Foreground="#374151" TextWrapping="Wrap" Margin="0,0,0,14"
                       Text="Review the run. No element ever moves physically — hosted/face-based elements only get their Level parameter changed, while MEP curves and freestanding elements also get their offset recalculated to preserve their exact height. Everything is wrapped in one undoable step (Ctrl+Z)."/>
            <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Margin="0,0,0,14">
              <StackPanel>
                <Border BorderBrush="#f0f1ff" BorderThickness="0,0,0,1" Padding="13,10"><Grid>
                  <TextBlock Text="Active levels" FontSize="12.5" Foreground="#6b7280" VerticalAlignment="Center"/>
                  <TextBlock x:Name="R_Levels" FontSize="13" FontWeight="SemiBold" Foreground="#1f2937" HorizontalAlignment="Right" VerticalAlignment="Center"/></Grid></Border>
                <Border BorderBrush="#f0f1ff" BorderThickness="0,0,0,1" Padding="13,10"><Grid>
                  <TextBlock Text="Scope" FontSize="12.5" Foreground="#6b7280" VerticalAlignment="Center"/>
                  <TextBlock x:Name="R_Scope" FontSize="13" FontWeight="SemiBold" Foreground="#1f2937" HorizontalAlignment="Right" VerticalAlignment="Center"/></Grid></Border>
                <Border BorderBrush="#f0f1ff" BorderThickness="0,0,0,1" Padding="13,10"><Grid>
                  <TextBlock Text="Categories" FontSize="12.5" Foreground="#6b7280" VerticalAlignment="Center"/>
                  <TextBlock x:Name="R_Categories" FontSize="13" FontWeight="SemiBold" Foreground="#1f2937" HorizontalAlignment="Right" VerticalAlignment="Center" TextWrapping="Wrap" MaxWidth="380"/></Grid></Border>
                <Border Padding="13,10"><Grid>
                  <TextBlock Text="Elements to check" FontSize="12.5" Foreground="#6b7280" VerticalAlignment="Center"/>
                  <TextBlock x:Name="R_ElementCount" FontFamily="Consolas" FontSize="13" FontWeight="SemiBold" Foreground="#1e248c" HorizontalAlignment="Right" VerticalAlignment="Center"/></Grid></Border>
              </StackPanel>
            </Border>

            <Border x:Name="GroupCard" Background="#fafbff" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="12,10" Margin="0,0,0,14" Visibility="Collapsed">
              <StackPanel>
                <Grid Margin="0,0,0,8">
                  <TextBlock x:Name="GroupLabelTB" FontSize="13.5" Foreground="#374151" VerticalAlignment="Center" TextWrapping="Wrap"/>
                  <ToggleButton x:Name="GroupToggle" HorizontalAlignment="Right" IsChecked="False"
                                Width="38" Height="22" Cursor="Hand" BorderThickness="0">
                    <ToggleButton.Template>
                      <ControlTemplate TargetType="ToggleButton">
                        <Border x:Name="Tr" Width="38" Height="22" CornerRadius="11"
                                Background="#cbd0e0" Padding="2">
                          <Border x:Name="Th" Width="18" Height="18" CornerRadius="9"
                                  Background="White" HorizontalAlignment="Left">
                            <Border.Effect>
                              <DropShadowEffect Color="#000000" BlurRadius="3" ShadowDepth="1" Opacity="0.18"/>
                            </Border.Effect>
                          </Border>
                        </Border>
                        <ControlTemplate.Triggers>
                          <Trigger Property="IsChecked" Value="True">
                            <Setter TargetName="Tr" Property="Background" Value="#44b8d3"/>
                            <Setter TargetName="Th" Property="HorizontalAlignment" Value="Right"/>
                          </Trigger>
                        </ControlTemplate.Triggers>
                      </ControlTemplate>
                    </ToggleButton.Template>
                  </ToggleButton>
                </Grid>
                <StackPanel Orientation="Horizontal">
                  <TextBlock Text="&#x26A0;" FontSize="12" Foreground="#c8850d" Margin="0,0,7,0" VerticalAlignment="Center"/>
                  <TextBlock Text="Elements inside Model Groups are skipped unless this is on."
                             FontSize="11.5" Foreground="#9aa0ac" TextWrapping="Wrap"/>
                </StackPanel>
              </StackPanel>
            </Border>

            <StackPanel Orientation="Horizontal" Margin="2,0,0,0">
              <TextBlock Text="i" FontSize="12" FontWeight="Bold" Foreground="#44b8d3" Margin="0,0,7,0" VerticalAlignment="Center"/>
              <TextBlock Text="Elements checked out by another user are always skipped. Host-driven elements (e.g. doors/windows) are only skipped when their level can't be edited independently of the host."
                         FontSize="11.5" Foreground="#9aa0ac" TextWrapping="Wrap"/>
            </StackPanel>
          </StackPanel>

          <!-- Result panel -->
          <StackPanel x:Name="ResultPanel" Visibility="Collapsed">
            <StackPanel HorizontalAlignment="Center" Margin="0,4,0,18">
              <Border Width="52" Height="52" CornerRadius="26" Background="#e4f7f0"
                      HorizontalAlignment="Center" Margin="0,0,0,12">
                <TextBlock Text="&#x2713;" FontSize="26" FontWeight="Bold" Foreground="#22b07c"
                           HorizontalAlignment="Center" VerticalAlignment="Center"/>
              </Border>
              <TextBlock Text="Levels assigned" FontSize="18" FontWeight="Bold"
                         Foreground="#1e248c" HorizontalAlignment="Center"/>
              <TextBlock x:Name="Res_Summary" FontSize="12.5" Foreground="#6b7280"
                         HorizontalAlignment="Center" Margin="0,3,0,0" TextWrapping="Wrap" TextAlignment="Center"/>
            </StackPanel>

            <TextBlock Text="ELEMENTS UPDATED PER CATEGORY" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,7"/>
            <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Margin="0,0,0,14">
              <StackPanel x:Name="ResultCategoriesPanel"/>
            </Border>

            <Border x:Name="ResultSkippedCard" Background="#fafbff" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Margin="0,0,0,14" Visibility="Collapsed">
              <StackPanel x:Name="ResultSkippedPanel"/>
            </Border>

            <StackPanel Orientation="Horizontal">
              <TextBlock Text="&#x2139;" FontSize="12" Foreground="#44b8d3" Margin="0,0,7,0" VerticalAlignment="Center"/>
              <TextBlock Text="A full report with clickable Element Id links was printed to the pyRevit output console."
                         FontSize="11.5" Foreground="#9aa0ac" TextWrapping="Wrap"/>
            </StackPanel>
          </StackPanel>

        </StackPanel>
      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Border Grid.Row="4" Background="White" BorderBrush="#e8eaff" BorderThickness="0,1,0,0" Padding="20,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="StepLabel" Grid.Column="0" Text="Step 1 of 4"
                   FontFamily="Consolas" FontSize="12" Foreground="#9aa0ac" VerticalAlignment="Center"/>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <Button x:Name="CancelBtn" Content="Cancel" Style="{StaticResource GhostBtn}"/>
          <Button x:Name="BackBtn"   Content="&#x25C0;  Back"   Style="{StaticResource GhostBtn}"  Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="NextBtn"   Content="Next  &#x25B6;"   Style="{StaticResource PrimaryBtn}" Margin="8,0,0,0"/>
          <Button x:Name="RunBtn"    Content="&#x25B6;  Run"    Style="{StaticResource PrimaryBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="AgainBtn"  Content="&#x21BA;  Run again" Style="{StaticResource GhostBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="DoneBtn"   Content="Done" Style="{StaticResource PrimaryBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
        </StackPanel>
      </Grid>
    </Border>

  </Grid>
</Window>
"""

# ─────────────────────────────────────────────────────────────────────────────
# WIZARD DIALOG
# ─────────────────────────────────────────────────────────────────────────────

BANNERS = [
    u"Choose which levels are valid targets. Elements are linked to the highest active level at or below their real height — their physical position never changes.",
    u"Choose which elements to process — your current selection, everything in the active view, or the whole model.",
    u"Choose which categories to update within the selected scope.",
    u"Review the plan, then run. Every change is wrapped in a single undoable step.",
]

SCOPE_DEFS = [
    (SCOPE_SELECTION, u"Current Selection", u"Only the elements currently selected in the model"),
    (SCOPE_VIEW, u"Active View", u"Every model element visible in the current view"),
    (SCOPE_MODEL, u"Entire Model", u"Every model element in the whole project"),
]


class AutoAssignLevelDialog(object):

    def __init__(self, all_levels):
        self._all_levels = all_levels
        self._step = 1
        self._done = False
        self.cancelled = True
        self._window = None
        self._result = None

        default_active = get_default_active_levels(all_levels)
        self._level_on = dict((l.Name, l.Name in default_active) for l in all_levels)
        self._level_rows = []

        self._scope = SCOPE_SELECTION
        self._scope_cache = {}
        self._scope_rows = []

        self._category_on = {}
        self._category_rows = []
        self._categories_dict = {}
        self._categories_scope_key = None

        self._process_groups = False
        self._group_count = 0

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self):
        ctx = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        w.FindName(u"CloseBtn").Click += lambda s, e: w.Close()
        w.FindName(u"CancelBtn").Click += self._on_cancel
        w.FindName(u"BackBtn").Click += self._on_back
        w.FindName(u"NextBtn").Click += self._on_next
        w.FindName(u"RunBtn").Click += self._on_run
        w.FindName(u"AgainBtn").Click += self._on_again
        w.FindName(u"DoneBtn").Click += lambda s, e: w.Close()
        w.FindName(u"ToggleAllLevelsBtn").Click += self._on_toggle_all_levels
        w.FindName(u"ToggleAllCatBtn").Click += self._on_toggle_all_categories

        self._populate_levels()
        self._populate_scopes()
        self._go_to_step(1)
        return window

    # ── Step 1: levels ───────────────────────────────────────────────────────

    def _populate_levels(self):
        panel = self._window.FindName(u"LevelListPanel")
        panel.Children.Clear()
        self._level_rows = []
        for i, level in enumerate(self._all_levels):
            is_last = (i == len(self._all_levels) - 1)
            row_data = self._make_check_row(level.Name, format_elevation(level), is_last,
                                             lambda name=level.Name: self._level_on[name],
                                             lambda v, name=level.Name: self._level_on.__setitem__(name, v),
                                             self._on_level_toggled)
            panel.Children.Add(row_data[u"border"])
            self._level_rows.append(row_data)
        self._update_levels_label()

    def _on_level_toggled(self):
        self._update_levels_label()
        self._update_next_enabled()

    def _update_levels_label(self):
        on_count = sum(1 for v in self._level_on.values() if v)
        self._window.FindName(u"LevelsLabel").Text = (
            u"ACTIVE LEVELS · {} OF {} SELECTED".format(on_count, len(self._all_levels)))
        all_on = all(self._level_on.values()) if self._level_on else False
        self._window.FindName(u"ToggleAllLevelsBtn").Content = u"Deselect all" if all_on else u"Select all"

    def _on_toggle_all_levels(self, s, e):
        all_on = all(self._level_on.values()) if self._level_on else False
        new_state = not all_on
        for name in self._level_on:
            self._level_on[name] = new_state
        for row in self._level_rows:
            row[u"refresh_cb"]()
        self._update_levels_label()
        self._update_next_enabled()

    # ── Shared checkbox-row builder (levels + categories) ───────────────────

    def _make_check_row(self, title, subtitle, is_last, get_on, set_on, on_change):
        outer = WC.Border()
        outer.Padding = System.Windows.Thickness(12, 10, 12, 10)
        outer.Background = WM.Brushes.Transparent
        outer.Cursor = WI.Cursors.Hand
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        c0 = WC.ColumnDefinition()
        c1 = WC.ColumnDefinition()
        c0.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c0)
        grid.ColumnDefinitions.Add(c1)

        left = WC.StackPanel()
        left.Orientation = WC.Orientation.Horizontal
        WC.Grid.SetColumn(left, 0)

        cb_outer = WC.Border()
        cb_outer.Width = 18
        cb_outer.Height = 18
        cb_outer.CornerRadius = System.Windows.CornerRadius(5)
        cb_outer.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Margin = System.Windows.Thickness(0, 0, 10, 0)
        cb_outer.Cursor = WI.Cursors.Hand

        cb_check = WC.TextBlock()
        cb_check.Text = u"✓"
        cb_check.FontSize = 11
        cb_check.FontWeight = System.Windows.FontWeights.Bold
        cb_check.Foreground = WM.Brushes.White
        cb_check.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb_check.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Child = cb_check

        name_tb = WC.TextBlock()
        name_tb.Text = title
        name_tb.FontFamily = WM.FontFamily(u"Consolas")
        name_tb.FontSize = 12.5
        name_tb.FontWeight = System.Windows.FontWeights.SemiBold
        name_tb.Foreground = _color(BODY_COLOR)
        name_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center

        left.Children.Add(cb_outer)
        left.Children.Add(name_tb)

        sub_tb = WC.TextBlock()
        sub_tb.Text = subtitle
        sub_tb.FontSize = 12
        sub_tb.Foreground = _color(MUTED_COLOR)
        sub_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(sub_tb, 1)

        grid.Children.Add(left)
        grid.Children.Add(sub_tb)
        outer.Child = grid

        row_data = {u"border": outer, u"cb_outer": cb_outer, u"cb_check": cb_check}

        def _refresh_cb():
            if get_on():
                cb_outer.Background = _color(NAVY_COLOR)
                cb_outer.BorderBrush = _color(NAVY_COLOR)
                cb_outer.BorderThickness = System.Windows.Thickness(1.5)
                cb_check.Visibility = System.Windows.Visibility.Visible
            else:
                cb_outer.Background = WM.Brushes.White
                cb_outer.BorderBrush = _color(GRAY_COLOR)
                cb_outer.BorderThickness = System.Windows.Thickness(1.5)
                cb_check.Visibility = System.Windows.Visibility.Collapsed

        row_data[u"refresh_cb"] = _refresh_cb
        _refresh_cb()

        def on_click(s, e):
            set_on(not get_on())
            _refresh_cb()
            on_change()

        outer.MouseLeftButtonUp += on_click
        return row_data

    # ── Step 2: scope ────────────────────────────────────────────────────────

    def _populate_scopes(self):
        panel = self._window.FindName(u"ScopeListPanel")
        panel.Children.Clear()
        self._scope_rows = []
        for i, (key, title, subtitle) in enumerate(SCOPE_DEFS):
            is_last = (i == len(SCOPE_DEFS) - 1)
            border, icon_tb, count_tb = self._make_scope_row(key, title, subtitle, is_last)
            panel.Children.Add(border)
            self._scope_rows.append((key, border, icon_tb, count_tb))

    def _make_scope_row(self, key, title, subtitle, is_last):
        border = WC.Border()
        border.Padding = System.Windows.Thickness(12, 10, 12, 10)
        border.Background = WM.Brushes.Transparent
        border.Cursor = WI.Cursors.Hand
        if not is_last:
            border.BorderBrush = _color(LINE_COLOR)
            border.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        c0 = WC.ColumnDefinition()
        c1 = WC.ColumnDefinition()
        c0.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c0)
        grid.ColumnDefinitions.Add(c1)

        left = WC.StackPanel()
        left.Orientation = WC.Orientation.Horizontal
        WC.Grid.SetColumn(left, 0)

        icon_tb = WC.TextBlock()
        icon_tb.Text = u"○"
        icon_tb.FontSize = 14
        icon_tb.Foreground = _color(GRAY_COLOR)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        icon_tb.Margin = System.Windows.Thickness(0, 0, 10, 0)

        texts = WC.StackPanel()
        title_tb = WC.TextBlock()
        title_tb.Text = title
        title_tb.FontSize = 13.5
        title_tb.Foreground = _color(BODY_COLOR)
        sub_tb = WC.TextBlock()
        sub_tb.Text = subtitle
        sub_tb.FontSize = 11.5
        sub_tb.Foreground = _color(MUTED_COLOR)
        sub_tb.Margin = System.Windows.Thickness(0, 1, 0, 0)
        texts.Children.Add(title_tb)
        texts.Children.Add(sub_tb)

        left.Children.Add(icon_tb)
        left.Children.Add(texts)

        count_border = WC.Border()
        count_border.Background = _color(WM.Color.FromRgb(0xec, 0xf8, 0xfc))
        count_border.BorderBrush = _color(WM.Color.FromRgb(0xb8, 0xe8, 0xf2))
        count_border.BorderThickness = System.Windows.Thickness(1)
        count_border.CornerRadius = System.Windows.CornerRadius(10)
        count_border.Padding = System.Windows.Thickness(8, 3, 8, 3)
        count_border.VerticalAlignment = System.Windows.VerticalAlignment.Center
        count_tb = WC.TextBlock()
        count_tb.Text = u"…"
        count_tb.FontSize = 11
        count_tb.FontWeight = System.Windows.FontWeights.SemiBold
        count_tb.Foreground = _color(CYAN_COLOR)
        count_border.Child = count_tb
        WC.Grid.SetColumn(count_border, 1)

        grid.Children.Add(left)
        grid.Children.Add(count_border)
        border.Child = grid

        def on_click(s, e, k=key):
            self._select_scope(k)

        border.MouseLeftButtonUp += on_click
        return border, icon_tb, count_tb

    def _get_scope_elements(self, key):
        if key not in self._scope_cache:
            self._scope_cache[key] = get_scope_elements(key)
        return self._scope_cache[key]

    def _select_scope(self, key):
        self._scope = key
        elements = self._get_scope_elements(key)

        for k, border, icon_tb, count_tb in self._scope_rows:
            count = len(self._get_scope_elements(k)) if k in self._scope_cache else None
            if count is not None:
                count_tb.Text = u"{} element{}".format(count, u"" if count == 1 else u"s")
            selected = (k == self._scope)
            if selected:
                border.Background = _color(SEL_BG)
                icon_tb.Text = u"✓"
                icon_tb.Foreground = _color(CYAN_COLOR)
            else:
                border.Background = WM.Brushes.Transparent
                icon_tb.Text = u"○"
                icon_tb.Foreground = _color(GRAY_COLOR)

        warn_border = self._window.FindName(u"ScopeWarningBorder")
        warn_tb = self._window.FindName(u"ScopeWarningTB")
        if not elements:
            warn_tb.Text = u"No model elements were found in this scope. Choose a different scope or Cancel."
            warn_border.Visibility = System.Windows.Visibility.Visible
        else:
            warn_border.Visibility = System.Windows.Visibility.Collapsed

        self._update_next_enabled()

    # ── Step 3: categories ───────────────────────────────────────────────────

    def _refresh_categories(self):
        elements = self._get_scope_elements(self._scope)
        if self._categories_scope_key != self._scope:
            self._categories_dict = group_by_category(elements)
            self._category_on = dict((name, True) for name in self._categories_dict)
            self._categories_scope_key = self._scope

        panel = self._window.FindName(u"CategoryListPanel")
        panel.Children.Clear()
        self._category_rows = []
        names = sorted(self._categories_dict.keys())
        for i, name in enumerate(names):
            is_last = (i == len(names) - 1)
            count = len(self._categories_dict[name])
            row_data = self._make_check_row(
                name, u"{} element{}".format(count, u"" if count == 1 else u"s"), is_last,
                lambda n=name: self._category_on[n],
                lambda v, n=name: self._category_on.__setitem__(n, v),
                self._on_category_toggled)
            panel.Children.Add(row_data[u"border"])
            self._category_rows.append(row_data)
        self._update_categories_label()

    def _on_category_toggled(self):
        self._update_categories_label()
        self._update_next_enabled()

    def _update_categories_label(self):
        total = len(self._categories_dict)
        on_count = sum(1 for v in self._category_on.values() if v)
        self._window.FindName(u"CategoriesLabel").Text = (
            u"CATEGORIES · {} OF {} SELECTED".format(on_count, total))
        all_on = all(self._category_on.values()) if self._category_on else False
        self._window.FindName(u"ToggleAllCatBtn").Content = u"Deselect all" if all_on else u"Select all"

    def _on_toggle_all_categories(self, s, e):
        all_on = all(self._category_on.values()) if self._category_on else False
        new_state = not all_on
        for name in self._category_on:
            self._category_on[name] = new_state
        for row in self._category_rows:
            row[u"refresh_cb"]()
        self._update_categories_label()
        self._update_next_enabled()

    def _selected_categories(self):
        return [name for name, on in self._category_on.items() if on]

    def _elements_to_process(self):
        elements = []
        for name in self._selected_categories():
            elements.extend(self._categories_dict.get(name, []))
        return elements

    # ── Step 4: review ───────────────────────────────────────────────────────

    def _refresh_review(self):
        w = self._window
        active_levels = [l for l in self._all_levels if self._level_on.get(l.Name)]
        elements = self._elements_to_process()
        cats = self._selected_categories()

        w.FindName(u"R_Levels").Text = u"{} of {}".format(len(active_levels), len(self._all_levels))
        scope_title = dict((k, t) for k, t, _ in SCOPE_DEFS)[self._scope]
        w.FindName(u"R_Scope").Text = scope_title
        w.FindName(u"R_Categories").Text = u"{} selected — {}".format(len(cats), u", ".join(sorted(cats)))
        w.FindName(u"R_ElementCount").Text = u"{}".format(len(elements))

        grouped = [e for e in elements if e.GroupId != DB.ElementId.InvalidElementId]
        self._group_count = len(grouped)
        group_card = w.FindName(u"GroupCard")
        if self._group_count:
            w.FindName(u"GroupLabelTB").Text = u"Update {} element(s) inside Model Groups".format(self._group_count)
            group_card.Visibility = System.Windows.Visibility.Visible
        else:
            group_card.Visibility = System.Windows.Visibility.Collapsed

        run_btn = w.FindName(u"RunBtn")
        run_btn.IsEnabled = len(elements) > 0 and len(active_levels) > 0

    # ── Navigation ───────────────────────────────────────────────────────────

    def _go_to_step(self, step):
        self._step = step
        vis = System.Windows.Visibility.Visible
        col = System.Windows.Visibility.Collapsed
        w = self._window

        w.FindName(u"Step1Panel").Visibility = vis if step == 1 else col
        w.FindName(u"Step2Panel").Visibility = vis if step == 2 else col
        w.FindName(u"Step3Panel").Visibility = vis if step == 3 else col
        w.FindName(u"Step4Panel").Visibility = vis if step == 4 else col

        w.FindName(u"CancelBtn").Visibility = vis if step == 1 else col
        w.FindName(u"BackBtn").Visibility = vis if step > 1 and not self._done else col
        w.FindName(u"NextBtn").Visibility = vis if step < 4 and not self._done else col
        w.FindName(u"RunBtn").Visibility = vis if step == 4 and not self._done else col
        w.FindName(u"AgainBtn").Visibility = vis if self._done else col
        w.FindName(u"DoneBtn").Visibility = vis if self._done else col

        w.FindName(u"StepLabel").Text = u"" if self._done else u"Step {} of 4".format(step)

        if step == 2 and self._scope not in self._scope_cache:
            self._select_scope(self._scope)
        if step == 3:
            self._refresh_categories()
        if step == 4 and not self._done:
            self._refresh_review()

        self._update_next_enabled()
        self._update_stepper(step)
        self._update_banner(step)

    def _update_stepper(self, step):
        circles = [(u"SC1", u"SN1", u"SL1"), (u"SC2", u"SN2", u"SL2"),
                   (u"SC3", u"SN3", u"SL3"), (u"SC4", u"SN4", u"SL4")]
        for i, (cn, nn, ln) in enumerate(circles):
            s = i + 1
            circle = self._window.FindName(cn)
            num_tb = self._window.FindName(nn)
            lbl_tb = self._window.FindName(ln)
            if self._done or s < step:
                circle.Background = _color(GREEN_COLOR)
                num_tb.Text = u"✓"
                lbl_tb.Foreground = _color(GREEN_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.SemiBold
            elif s == step:
                circle.Background = _color(NAVY_COLOR)
                num_tb.Text = str(s)
                lbl_tb.Foreground = _color(NAVY_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.SemiBold
            else:
                circle.Background = _color(GRAY_COLOR)
                num_tb.Text = str(s)
                lbl_tb.Foreground = _color(MUTED_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.Normal

    def _update_banner(self, step):
        w = self._window
        banner_border = w.FindName(u"BannerBorder")
        banner_text = w.FindName(u"BannerText")
        if self._done:
            banner_border.Visibility = System.Windows.Visibility.Collapsed
        else:
            banner_border.Visibility = System.Windows.Visibility.Visible
            banner_text.Text = BANNERS[step - 1]

    def _update_next_enabled(self):
        next_btn = self._window.FindName(u"NextBtn")
        if self._step == 1:
            next_btn.IsEnabled = any(self._level_on.values())
        elif self._step == 2:
            next_btn.IsEnabled = len(self._get_scope_elements(self._scope)) > 0
        elif self._step == 3:
            next_btn.IsEnabled = any(self._category_on.values())
        else:
            next_btn.IsEnabled = True

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_cancel(self, s, e):
        self.cancelled = True
        self._window.Close()

    def _on_back(self, s, e):
        if self._step > 1:
            self._go_to_step(self._step - 1)

    def _on_next(self, s, e):
        if self._step == 1:
            active_names = set(name for name, on in self._level_on.items() if on)
            save_ignored_levels(self._all_levels, active_names)
        if self._step < 4:
            self._go_to_step(self._step + 1)

    def _on_run(self, s, e):
        w = self._window
        run_btn = w.FindName(u"RunBtn")
        run_btn.IsEnabled = False
        run_btn.Content = u"Running..."

        active_levels = sorted([l for l in self._all_levels if self._level_on.get(l.Name)],
                                key=lambda l: l.Elevation)
        elements = self._elements_to_process()
        process_groups = bool(w.FindName(u"GroupToggle").IsChecked) if self._group_count else False

        try:
            result = run_assignment(elements, active_levels, process_groups)
            self._result = result
            print_console_report(result)
            self._show_result(result)
            self.cancelled = False
            try:
                self._window.Topmost = True
                self._window.Activate()
                self._window.Topmost = False
            except Exception:
                pass
        except Exception:
            run_btn.IsEnabled = True
            run_btn.Content = u"▶  Run"
            forms.alert(
                u"Auto Assign Level failed, all changes rolled back:\n\n{}".format(traceback.format_exc()),
                title=u"Auto Assign Level — Error")

    def _show_result(self, result):
        self._done = True
        w = self._window
        updated_stats = result[u"updated_stats"]
        total = sum(updated_stats.values())

        w.FindName(u"ReviewPanel").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"ResultPanel").Visibility = System.Windows.Visibility.Visible

        w.FindName(u"Res_Summary").Text = (
            u"{} element(s) updated across {} categor{}.".format(
                total, len(updated_stats), u"y" if len(updated_stats) == 1 else u"ies")
            if total else u"All checked elements were already on the correct level.")

        cp = w.FindName(u"ResultCategoriesPanel")
        cp.Children.Clear()
        names = sorted(updated_stats.keys())
        for i, name in enumerate(names):
            is_last = (i == len(names) - 1)
            cp.Children.Add(self._make_result_row(name, updated_stats[name], is_last))
        if not names:
            cp.Children.Add(self._make_result_row(u"—", 0, True))

        skip_rows = [
            (u"Skipped — inside Model Groups", result[u"skipped_groups"]),
            (u"Skipped — checked out by another user", result[u"skipped_workshare"]),
            (u"Skipped — level locked to host", result[u"skipped_hosted"]),
            (u"Skipped — sloped run", result[u"skipped_sloped"]),
            (u"Skipped — unexpected error", result[u"skipped_errors"]),
        ]
        skip_rows = [(label, count) for label, count in skip_rows if count > 0]
        skip_card = w.FindName(u"ResultSkippedCard")
        sp = w.FindName(u"ResultSkippedPanel")
        sp.Children.Clear()
        if skip_rows:
            for i, (label, count) in enumerate(skip_rows):
                is_last = (i == len(skip_rows) - 1)
                sp.Children.Add(self._make_result_row(label, count, is_last))
            skip_card.Visibility = System.Windows.Visibility.Visible
        else:
            skip_card.Visibility = System.Windows.Visibility.Collapsed

        self._go_to_step(4)

    def _make_result_row(self, label, count, is_last):
        row = WC.Border()
        row.Padding = System.Windows.Thickness(13, 10, 13, 10)
        if not is_last:
            row.BorderBrush = _color(LINE_COLOR)
            row.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)
        grid = WC.Grid()
        c0 = WC.ColumnDefinition()
        c1 = WC.ColumnDefinition()
        c0.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c0)
        grid.ColumnDefinitions.Add(c1)
        label_tb = WC.TextBlock()
        label_tb.Text = label
        label_tb.FontSize = 12.5
        label_tb.Foreground = _color(BODY_COLOR)
        label_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        count_tb = WC.TextBlock()
        count_tb.Text = str(count)
        count_tb.FontFamily = WM.FontFamily(u"Consolas")
        count_tb.FontSize = 12.5
        count_tb.FontWeight = System.Windows.FontWeights.Bold
        count_tb.Foreground = _color(NAVY_COLOR)
        count_tb.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        count_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(count_tb, 1)
        grid.Children.Add(label_tb)
        grid.Children.Add(count_tb)
        row.Child = grid
        return row

    def _on_again(self, s, e):
        self._done = False
        self._result = None
        self._scope_cache = {}
        self._categories_scope_key = None
        w = self._window
        w.FindName(u"ReviewPanel").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"ResultPanel").Visibility = System.Windows.Visibility.Collapsed
        self._go_to_step(1)

    # ── Show ─────────────────────────────────────────────────────────────────

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
        window = self._build()
        frame = DispatcherFrame()

        # Keep the wizard above Revit's main window and always on top —
        # otherwise it's easy to lose the dialog behind Revit, or have it
        # rendered on top but not actually own input focus, so clicks on
        # it silently do nothing. UIApplication.MainWindowHandle is the
        # documented Revit API handle for exactly this (an AdWindows.
        # ComponentManager-based Owner assignment was found to fail
        # silently in this same pattern elsewhere in the plugin).
        try:
            WindowInteropHelper(window).Owner = revit.uiapp.MainWindowHandle
        except Exception:
            pass
        window.Topmost = True

        def on_closed(s, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        window.Activate()
        Dispatcher.PushFrame(frame)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if __shiftclick__:
        quick_run()
        return

    all_levels = get_all_levels()
    if not all_levels:
        forms.alert(u"No levels found in this project.", title=u"Auto Assign Level")
        return

    dlg = AutoAssignLevelDialog(all_levels)
    dlg.show()


main()
