# -*- coding: utf-8 -*-
"""Coordination Graphics — EasyBIM BIM Management. (V2)

Sets up a clash-coordination look in the active 2D plan view: identifies the
Architecture, Structure (and optional Traffic) Revit links (remembered choice
> auto-detect by keyword > manual WPF pick), applies a shared "Coordination -
Arch vs Str" view template for the host clutter/annotation clean-up, hides
every other link, deep-scans the Arch/Struct links' Walls/Columns/Structural
Framing/Foundations for concrete-material types, and colors those types red
(Structure) / blue (Architecture) via two View Filters — driven by
Settings.json (see "Coordination Settings" and lib/easybim/coordination_settings.py).

Engine: IronPython 2.7 (no "#! python3" shebang) — matches the tab's other
WPF+Transaction buttons.

WHY VIEW FILTERS INSTEAD OF PER-LINK GRAPHIC OVERRIDES (read before changing
Step 8's approach): V1 of this tool used View.SetElementOverrides per element
collected from each link's own document — undocumented for elements outside
the view's own document, and RevitLinkGraphicsSettings (the "official" link
graphics API) doesn't expose per-category control even on Revit 2024+ (this
extension targets 2023+). View Filters sidestep this entirely: a
ParameterFilterElement rule matching "Type Name" is evaluated against ANY
element the view can see, host or linked, as long as the link's display is
"By Host View" (the default) — which is standard, well-documented Revit
behavior, not a workaround. The trade-off: a filter can't distinguish WHICH
link an element came from, only its own parameter values — hence the deep
Type-Name scan per link and two separate filters (Structure vs Architecture),
which works as long as the two disciplines don't reuse the exact same type
name (a acceptable, disclosed edge case).

WHY MOST CATEGORY VISIBILITY LIVES ON THE TEMPLATE, NOT THE VIEW: the View
Template "include/exclude" list groups ALL model categories under one toggle
and ALL annotation categories under another (there is no per-category
granularity) — so Step 3's "host clutter" hides and Step 6's "Floors/Topo/
Dimensions/etc." hides are the same toggle and have to live in the same
place. They're set on the template. Individual RevitLinkInstance visibility
(Step 5) and halftone (Step 7) are element-level view overrides, which are
NEVER template-controlled regardless — those stay on the active view.

WHY EXCLUDING "V/G Overrides RVT Links" FROM TEMPLATE CONTROL IS BEST-EFFORT:
Autodesk's own API docs say this template row had "No API access (Revit
2020)" and a direct BuiltInParameter.VIS_GRAPHICS_RVT_LINKS lookup returns
null on the versions checked. This is attempted by parameter *name* instead
(more likely to keep working if a future API version adds support), wrapped
so a failure only adds a warning — it doesn't block anything, because link
visibility/halftone (Steps 5 & 7) don't depend on this template row at all.

WHY GRID COLORING USES PER-ELEMENT OVERRIDES, NOT A THIRD VIEW FILTER (V3):
Walls/Columns/Framing/Foundations can be told apart per-link by Type Name
(concrete keywords), which is why those use View Filters. Grids have no
comparable per-link distinguishing parameter — a rule-based filter can only
say "this is a Grid", not "this Grid came from the Structure link", so a
single Grids rule added to both the Structure and Architecture filters would
match every grid in both and the two colors couldn't be told apart. Grid
coloring instead reuses the same best-effort View.SetElementOverrides-per-
linked-element technique already used elsewhere here for the DWG-import and
Traffic-link hides — undocumented for cross-document elements, but grids are
few enough per model that this is low-risk even if unsupported (falls back
to a warning, not a crash). Host grids are hidden the same way — element-
level, not category-level — because hiding OST_Grids as a category (the
template) would also hide the links' grids, which must stay visible.
"""

__title__ = "Coordination\nGraphics"
__author__ = "EasyBIM"
__doc__ = "Color-code concrete Walls/Columns/Framing/Foundations in the Architecture and Structure links via View Filters."

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
from Autodesk.Revit.DB import Structure
from Autodesk.Revit.UI import TaskDialog

import System
import System.Collections.Generic as SCG
import System.Windows.Controls as WC
import System.Windows.Media as WM

from System.Windows.Markup import XamlReader
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import revit, script
from easybim import coordination_settings as cfgmod
from easybim import coordination_settings_ui as settings_ui
from easybim import ui as ebui

doc    = revit.doc
uidoc  = revit.uidoc
logger = script.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_NAME = u"Coordination - Arch vs Str"

FILTER_NAME_STRUCT = u"EasyBIM - Structure Concrete"
FILTER_NAME_ARCH   = u"EasyBIM - Architecture Concrete"

GRAY_COLOR = DB.Color(190, 190, 190)

# Categories color-coded via the two View Filters (Step 6/8) — and given the
# Step 8A gray fallback on the template.
OVERRIDE_CATEGORY_NAMES = [
    "OST_Walls",
    "OST_Columns",
    "OST_StructuralColumns",
    "OST_StructuralFoundation",
    "OST_StructuralFraming",
]

# Host-model categories hidden on the template to declutter (Step 3). Not
# exhaustive — edit freely.
HOST_CLUTTER_CATEGORY_NAMES = [
    "OST_Furniture",
    "OST_FurnitureSystems",
    "OST_Casework",
    "OST_SpecialityEquipment",
    "OST_Planting",
    "OST_Entourage",
    "OST_Site",
    "OST_Parking",
    "OST_Sections",
    "OST_Elevations",
    "OST_Callouts",
    "OST_GenericModel",
    "OST_Mass",
]

# Model categories hidden on the template (Step 6). OST_Toposolid only exists
# from the 2024 API onward — resolved defensively at runtime.
LINK_MODEL_HIDE_CATEGORY_NAMES = [
    "OST_Floors",
    "OST_Topography",
    "OST_Toposolid",
]

WALL_NONCORE_CATEGORY_NAME = "OST_WallsNonCoreLayers"

# Annotation categories hidden on the template (Step 6). Room Tags are
# deliberately NOT here — kept visible explicitly, see _ensure_room_tags_visible.
LINK_ANNOTATION_HIDE_CATEGORY_NAMES = [
    "OST_Dimensions",
    "OST_ReferenceLines",
    "OST_CLines",              # "Reference Planes" — historical BuiltInCategory name
    "OST_ReferenceViewer",
    "OST_PlanRegion",
    "OST_VolumeOfInterest",    # Scope Boxes
]

# Categories left visible inside the Traffic link when enabled (Step 7).
TRAFFIC_KEEP_CATEGORY_NAMES = [
    "OST_Parking",
    "OST_SpotElevations",
    "OST_SpotSlopes",
]

BASEMENT_TOKENS = (u"BASEMENT", u"מרתף")
DWG_KEEP_TOKEN   = u"TR"

GRID_CATEGORY_NAME = "OST_Grids"

# Refinement 2 — automatic sheet creation
TITLEBLOCK_TOKEN   = u"EB_Title Block_A0"
SHEET_NUMBER_PREFIX= u"ARC/STR-"


def _elem_name(elem):
    """Safe Element/ElementType .Name getter. Plain attribute access on
    ElementType.Name (FamilySymbol, WallType, and other *Type classes) raises
    AttributeError in this Revit API binding, on both IronPython and CPython3
    pyrevit engines (see pyrevitlabs/pyRevit#854) — Element.Name.GetValue(elem)
    is the confirmed fix, tried first. SYMBOL_NAME_PARAM is a second-tier
    fallback (mainly for FamilySymbol) in case GetValue itself is ever
    unavailable in some Revit version; None on total failure rather than a
    raised exception, so a caller iterating many elements never crashes on
    one bad one."""
    if elem is None:
        return u""
    try:
        return elem.Name or u""
    except AttributeError:
        pass
    except Exception:
        return u""

    try:
        v = DB.Element.Name.GetValue(elem)
        if v:
            return v
    except Exception:
        pass

    try:
        bip = getattr(DB.BuiltInParameter, "SYMBOL_NAME_PARAM", None)
        if bip is not None:
            p = elem.get_Parameter(bip)
            if p is not None:
                return p.AsString() or u""
    except Exception:
        pass

    return u""


def _resolve_categories(names, warnings):
    """BuiltInCategory names -> list of enum values, skipping (and warning
    about) any name that doesn't exist in the loaded RevitAPI.dll instead of
    crashing the whole command on one unfamiliar/version-gated category."""
    out = []
    for name in names:
        bic = getattr(DB.BuiltInCategory, name, None)
        if bic is None:
            warnings.append(u"Category '{}' does not exist in this Revit version — skipped.".format(name))
            continue
        out.append(bic)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# WPF LINK PICKER — 4-STEP WIZARD  (matches the Solution Section design system:
# navy/cyan gradient header, light-cyan info banner, numbered step indicator,
# card-style sections, footer "Step X of N" + Cancel/Back/Next-Apply)
# ─────────────────────────────────────────────────────────────────────────────

PICKER_XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="ARC/STR Coordination"
  Width="540" Height="640"
  WindowStartupLocation="CenterScreen"
  ResizeMode="NoResize"
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
            <Border Background="{TemplateBinding Background}" CornerRadius="6"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#44b8d3"/>
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
      <Setter Property="BorderBrush"     Value="#e1e8ed"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="6" Padding="{TemplateBinding Padding}">
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

    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background"      Value="White"/>
      <Setter Property="BorderBrush"     Value="#E1E8ED"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius"    Value="8"/>
      <Setter Property="Padding"         Value="16,14"/>
      <Setter Property="Margin"          Value="0,0,0,14"/>
    </Style>

    <Style x:Key="SectionLabel" TargetType="TextBlock">
      <Setter Property="FontSize"   Value="10.5"/>
      <Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Foreground" Value="#9aa0ac"/>
      <Setter Property="Margin"     Value="0,0,0,6"/>
    </Style>

    <Style x:Key="ComboStyle" TargetType="ComboBox">
      <Setter Property="Height"   Value="32"/>
      <Setter Property="FontSize" Value="12.5"/>
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
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Border Grid.Column="0" Width="44" Height="44" CornerRadius="10" VerticalAlignment="Center"
                Background="#151b6e">
          <Grid Width="22" Height="22">
            <Border BorderBrush="White" BorderThickness="1.5" CornerRadius="2"
                    Width="14" Height="14" HorizontalAlignment="Left" VerticalAlignment="Bottom"/>
            <Border BorderBrush="White" BorderThickness="1.5" CornerRadius="2"
                    Width="14" Height="14" HorizontalAlignment="Right" VerticalAlignment="Top"/>
          </Grid>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="ARC/STR Coordination" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Set up clash-coordination graphics for the active view"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <StackPanel Grid.Column="2" Orientation="Horizontal" VerticalAlignment="Center">
          <Button x:Name="BtnGear" Content="&#9881;" Width="28" Height="28"
                  Background="Transparent" Foreground="White" BorderThickness="0"
                  FontSize="15" Cursor="Hand" Margin="0,0,4,0" ToolTip="Settings (keywords, colors, patterns)"/>
          <Button x:Name="BtnCloseX" Content="&#10005;" Width="28" Height="28"
                  Background="Transparent" Foreground="White" BorderThickness="0"
                  FontSize="13" Cursor="Hand"/>
        </StackPanel>
      </Grid>
    </Border>

    <!-- INFO BANNER -->
    <Border Grid.Row="1" Background="#ecf8fc" BorderBrush="#bbe5f0" BorderThickness="0,0,0,1" Padding="20,10">
      <StackPanel Orientation="Horizontal">
        <Border Width="18" Height="18" CornerRadius="9" Background="#44b8d3" VerticalAlignment="Top" Margin="0,1,10,0">
          <TextBlock Text="i" Foreground="White" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="InfoText" FontSize="11.5" Foreground="#1c6478" TextWrapping="Wrap"
                   VerticalAlignment="Center" Width="460"/>
      </StackPanel>
    </Border>

    <!-- STEP INDICATOR -->
    <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,16,0,10">
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge1" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum1" Text="1" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Active View" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector1" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge2" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum2" Text="2" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Select Links" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector2" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge3" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum3" Text="3" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Options" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
      <Border x:Name="Connector3" Width="30" Height="2" VerticalAlignment="Top" Margin="0,13,0,0"/>
      <StackPanel Width="86" HorizontalAlignment="Center">
        <Border x:Name="Badge4" Width="26" Height="26" CornerRadius="13" HorizontalAlignment="Center">
          <TextBlock x:Name="BadgeNum4" Text="4" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock Text="Summary" FontSize="9" Foreground="#9aa0ac" HorizontalAlignment="Center"
                   Margin="0,4,0,0" TextAlignment="Center"/>
      </StackPanel>
    </StackPanel>

    <!-- BODY -->
    <ScrollViewer Grid.Row="3" VerticalScrollBarVisibility="Auto" Padding="20,6,20,6">
      <Grid>

        <!-- STEP 1: ACTIVE VIEW -->
        <StackPanel x:Name="Step1Panel">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="ACTIVE VIEW" Style="{StaticResource SectionLabel}"/>
              <TextBlock x:Name="ViewNameText" FontSize="14" FontWeight="Bold" Foreground="#1e248c"/>
              <TextBlock x:Name="ViewTypeText" FontSize="11.5" Foreground="#6b7280" Margin="0,4,0,0"/>
              <TextBlock x:Name="ViewLevelText" FontSize="11.5" Foreground="#6b7280" Margin="0,2,0,0"/>
              <TextBlock x:Name="ViewBasementText" FontSize="11.5" Foreground="#c8850d" Margin="0,8,0,0" TextWrapping="Wrap"/>
            </StackPanel>
          </Border>
        </StackPanel>

        <!-- STEP 2: SELECT LINKS -->
        <StackPanel x:Name="Step2Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="ARCHITECTURE LINKS (SELECT ONE OR MORE)" Style="{StaticResource SectionLabel}"/>
              <ScrollViewer MaxHeight="120" VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="ArchLinksPanel"/>
              </ScrollViewer>
            </StackPanel>
          </Border>
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="STRUCTURE LINKS (SELECT ONE OR MORE)" Style="{StaticResource SectionLabel}"/>
              <ScrollViewer MaxHeight="120" VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="StructLinksPanel"/>
              </ScrollViewer>
            </StackPanel>
          </Border>
          <TextBlock x:Name="Step2Error" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        </StackPanel>

        <!-- STEP 3: OPTIONS / TRAFFIC LINK -->
        <StackPanel x:Name="Step3Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="TRAFFIC LINK (OPTIONAL)" Style="{StaticResource SectionLabel}"/>
              <CheckBox x:Name="TrafficCheck" FontSize="12"
                        Content="Show Parking &amp; Elevations from Traffic Link"/>
              <StackPanel x:Name="TrafficPanel" Margin="0,10,0,0" Visibility="Collapsed">
                <ComboBox x:Name="TrafficCombo" Style="{StaticResource ComboStyle}"/>
              </StackPanel>
            </StackPanel>
          </Border>
          <TextBlock x:Name="Step3Error" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        </StackPanel>

        <!-- STEP 4: FINAL SUMMARY -->
        <StackPanel x:Name="Step4Panel" Visibility="Collapsed">
          <Border Style="{StaticResource Card}">
            <StackPanel>
              <TextBlock Text="READY TO APPLY" Style="{StaticResource SectionLabel}"/>
              <TextBlock x:Name="SumView"    FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumArch"    FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumStruct"  FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock x:Name="SumTraffic" FontSize="12" Foreground="#374151" Margin="0,2,0,2" TextWrapping="Wrap"/>
              <TextBlock Text="Click Apply to set up the template, links, filters and coordination sheet."
                         FontSize="11" Foreground="#8b93a7" TextWrapping="Wrap" Margin="0,10,0,0"/>
            </StackPanel>
          </Border>
        </StackPanel>

      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Grid Grid.Row="4" Margin="20,10,20,18">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>
      <TextBlock x:Name="StepXOfY" Grid.Column="0" VerticalAlignment="Center"
                 FontSize="11" Foreground="#9aa0ac" FontWeight="SemiBold"/>
      <StackPanel Grid.Column="1" Orientation="Horizontal">
        <Button x:Name="BtnCancel" Content="Cancel" Style="{StaticResource GhostBtn}" Width="90" Margin="0,0,10,0"/>
        <Button x:Name="BtnBack"   Content="◄ Back" Style="{StaticResource GhostBtn}" Width="90" Margin="0,0,10,0"/>
        <Button x:Name="BtnNext"   Content="Next ►" Style="{StaticResource PrimaryBtn}" Width="120"/>
      </StackPanel>
    </Grid>
  </Grid>
</Window>
"""


def _brush(hex_color):
    return WM.BrushConverter().ConvertFromString(hex_color)


STEP_LABELS = (u"Active View", u"Select Links", u"Options", u"Final Summary")


class LinkPickerDialog(object):
    """4-step wizard: Active View -> Select Links -> Options/Traffic -> Summary.
    Architecture and Structure each support selecting multiple links.
    defaults = {'arch': (list_of_names, source_label), 'struct': (...),
    'traffic': (name_or_None, source_label), 'use_traffic': bool}"""

    def __init__(self, links, defaults, view_info):
        self.links         = links
        self.defaults      = defaults
        self.view_info     = view_info
        self.cancelled     = True
        self.arch_links    = []
        self.struct_links  = []
        self.traffic_link  = None
        self.use_traffic   = False
        self._window       = None
        self._by_name      = {}
        self._arch_checks  = []
        self._struct_checks = []
        self._step         = 1

    def _build(self):
        ctx    = SysXmlReader.Create(StringReader(PICKER_XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        # Step 1 — active view summary
        w.FindName(u"ViewNameText").Text  = self.view_info.get(u"name", u"")
        w.FindName(u"ViewTypeText").Text  = u"Type: {}".format(self.view_info.get(u"view_type", u""))
        w.FindName(u"ViewLevelText").Text = u"Level: {}".format(self.view_info.get(u"level", u"—"))
        if self.view_info.get(u"basement"):
            w.FindName(u"ViewBasementText").Text = (
                u"Detected as a basement view — imported DWGs containing “TR” will "
                u"stay visible inside the links.")
        else:
            w.FindName(u"ViewBasementText").Text = u""

        # Step 2 — link checklists (multi-select)
        names = sorted((li[u"name"] for li in self.links), key=lambda n: n.upper())
        by_name = {}
        for li in self.links:
            by_name.setdefault(li[u"name"], li)
        self._by_name = by_name

        arch_default_names, _arch_src     = self.defaults.get(u"arch", ([], u"no match"))
        struct_default_names, _struct_src = self.defaults.get(u"struct", ([], u"no match"))
        traffic_name, _traffic_src        = self.defaults.get(u"traffic", (None, u"no match"))

        self._arch_checks   = self._build_checklist(u"ArchLinksPanel", names, arch_default_names)
        self._struct_checks = self._build_checklist(u"StructLinksPanel", names, struct_default_names)

        traffic_combo = w.FindName(u"TrafficCombo")
        for n in names:
            traffic_combo.Items.Add(n)
        if traffic_name:
            traffic_combo.SelectedItem = traffic_name

        # Step 3 — traffic option
        traffic_check = w.FindName(u"TrafficCheck")
        traffic_panel = w.FindName(u"TrafficPanel")
        use_traffic_default = bool(self.defaults.get(u"use_traffic"))
        traffic_check.IsChecked = use_traffic_default
        traffic_panel.Visibility = (System.Windows.Visibility.Visible if use_traffic_default
                                     else System.Windows.Visibility.Collapsed)
        traffic_check.Checked   += lambda s, e: setattr(
            traffic_panel, u"Visibility", System.Windows.Visibility.Visible)
        traffic_check.Unchecked += lambda s, e: setattr(
            traffic_panel, u"Visibility", System.Windows.Visibility.Collapsed)

        w.FindName(u"BtnGear").Click   += self._on_settings
        w.FindName(u"BtnCloseX").Click += lambda s, e: window.Close()
        w.FindName(u"BtnCancel").Click += lambda s, e: window.Close()
        w.FindName(u"BtnBack").Click   += self._on_back
        w.FindName(u"BtnNext").Click   += self._on_next

        self._go_to_step(1)
        return window

    def _build_checklist(self, panel_name, names, checked_names):
        panel = self._window.FindName(panel_name)
        panel.Children.Clear()
        checked_set = set(checked_names or [])
        checks = []
        for n in names:
            cb = WC.CheckBox()
            cb.Content = n
            cb.FontSize = 12
            cb.Margin = System.Windows.Thickness(0, 3, 0, 3)
            cb.IsChecked = n in checked_set
            panel.Children.Add(cb)
            checks.append(cb)
        return checks

    def _checked_names(self, checks):
        return [cb.Content for cb in checks if cb.IsChecked]

    def _on_settings(self, sender, e):
        """Gear icon — the two tools were merged into one button, this is
        where the settings editor now lives. run() reloads Settings.json
        fresh after the wizard closes, so anything changed here is picked
        up for this same run without needing to reopen the tool."""
        settings_ui.SettingsDialog(cfgmod.load_settings()).show()

    def _go_to_step(self, step):
        self._step = step
        w = self._window

        panels = {1: u"Step1Panel", 2: u"Step2Panel", 3: u"Step3Panel", 4: u"Step4Panel"}
        for i, name in panels.items():
            w.FindName(name).Visibility = (System.Windows.Visibility.Visible if i == step
                                            else System.Windows.Visibility.Collapsed)

        info = {
            1: u"This tool modifies the ACTIVE view's visibility, categories, filters and view "
               u"template — confirm you're on the right view before continuing.",
            2: u"Pick one or more Architecture links and one or more Structure links. Pre-checked "
               u"from your last run or auto-detected by keyword — change either if needed.",
            3: u"Optional: also restrict a Traffic link to Parking, Spot Elevations and Spot "
               u"Slopes only, shown in halftone.",
            4: u"Review your choices, then click Apply to set up coordination graphics and "
               u"create the sheet.",
        }
        w.FindName(u"InfoText").Text = info[step]

        w.FindName(u"BtnBack").Visibility = (System.Windows.Visibility.Collapsed if step == 1
                                              else System.Windows.Visibility.Visible)
        w.FindName(u"BtnNext").Content = u"Apply" if step == 4 else u"Next ►"
        w.FindName(u"StepXOfY").Text = u"Step {} of 4 — {}".format(step, STEP_LABELS[step - 1])

        self._update_step_indicator()

        if step == 4:
            self._populate_summary()

    def _update_step_indicator(self):
        w = self._window
        for i in range(1, 5):
            badge  = w.FindName(u"Badge{}".format(i))
            num_tb = w.FindName(u"BadgeNum{}".format(i))
            if i < self._step:
                badge.Background  = _brush(u"#44b8d3")
                num_tb.Text       = u"✓"
                num_tb.Foreground = _brush(u"#FFFFFF")
            elif i == self._step:
                badge.Background  = _brush(u"#1e248c")
                num_tb.Text       = unicode(i)
                num_tb.Foreground = _brush(u"#FFFFFF")
            else:
                badge.Background  = _brush(u"#e1e8ed")
                num_tb.Text       = unicode(i)
                num_tb.Foreground = _brush(u"#9aa0ac")
        for i in range(1, 4):
            conn = w.FindName(u"Connector{}".format(i))
            conn.Background = _brush(u"#44b8d3") if i < self._step else _brush(u"#e1e8ed")

    def _populate_summary(self):
        w = self._window
        arch_names   = self._checked_names(self._arch_checks)
        struct_names = self._checked_names(self._struct_checks)
        use_traffic  = bool(w.FindName(u"TrafficCheck").IsChecked)
        traffic_name = w.FindName(u"TrafficCombo").SelectedItem if use_traffic else None

        w.FindName(u"SumView").Text    = u"Active view: {}".format(self.view_info.get(u"name", u""))
        w.FindName(u"SumArch").Text    = u"Architecture link(s): {}".format(u", ".join(arch_names) or u"—")
        w.FindName(u"SumStruct").Text  = u"Structure link(s): {}".format(u", ".join(struct_names) or u"—")
        w.FindName(u"SumTraffic").Text = (u"Traffic link: {}".format(traffic_name)
                                           if (use_traffic and traffic_name)
                                           else u"Traffic link: (not used)")

    def _on_back(self, sender, e):
        if self._step > 1:
            self._go_to_step(self._step - 1)

    def _on_next(self, sender, e):
        w = self._window

        if self._step == 2:
            err = w.FindName(u"Step2Error")
            arch_names   = self._checked_names(self._arch_checks)
            struct_names = self._checked_names(self._struct_checks)
            if not arch_names or not struct_names:
                err.Text = u"Select at least one Architecture link and at least one Structure link."
                return
            overlap = set(arch_names) & set(struct_names)
            if overlap:
                err.Text = u"{} cannot be both an Architecture and a Structure link.".format(
                    u", ".join(sorted(overlap)))
                return
            err.Text = u""

        elif self._step == 3:
            err = w.FindName(u"Step3Error")
            use_traffic = bool(w.FindName(u"TrafficCheck").IsChecked)
            if use_traffic:
                traffic_name = w.FindName(u"TrafficCombo").SelectedItem
                arch_names   = self._checked_names(self._arch_checks)
                struct_names = self._checked_names(self._struct_checks)
                if not traffic_name:
                    err.Text = u"Select a Traffic link, or uncheck the Traffic option."
                    return
                if traffic_name in arch_names or traffic_name in struct_names:
                    err.Text = u"The Traffic link must be different from the Architecture and Structure links."
                    return
            err.Text = u""

        elif self._step == 4:
            self._finish()
            return

        self._go_to_step(self._step + 1)

    def _finish(self):
        w = self._window
        arch_names   = self._checked_names(self._arch_checks)
        struct_names = self._checked_names(self._struct_checks)
        use_traffic  = bool(w.FindName(u"TrafficCheck").IsChecked)
        traffic_name = w.FindName(u"TrafficCombo").SelectedItem if use_traffic else None

        self.arch_links    = [self._by_name[n] for n in arch_names]
        self.struct_links  = [self._by_name[n] for n in struct_names]
        self.traffic_link  = self._by_name[traffic_name] if (use_traffic and traffic_name) else None
        self.use_traffic   = use_traffic
        self.cancelled = False
        w.Close()

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
        window = self._build()
        frame  = DispatcherFrame()

        def on_closed(s, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        Dispatcher.PushFrame(frame)


# ─────────────────────────────────────────────────────────────────────────────
# LINK IDENTIFICATION  (keyword auto-detect + persisted memory, feeds the wizard's Step 2/3 defaults)
# ─────────────────────────────────────────────────────────────────────────────

def get_all_link_instances():
    """One dict per placed RevitLinkInstance (not de-duplicated by file —
    each placed instance is a distinct pickable candidate)."""
    links = []
    for inst in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
        link_doc = None
        try:
            link_doc = inst.GetLinkDocument()
        except Exception:
            pass
        name  = _elem_name(inst)
        title = link_doc.Title if link_doc is not None else name
        links.append({
            u"instance": inst,
            u"id"      : inst.Id,
            u"doc"     : link_doc,
            u"name"    : name,
            u"title"   : title,
            u"loaded"  : link_doc is not None,
        })
    return links


def _matches_any(link_info, keywords):
    hay = u"{} {}".format(link_info[u"name"], link_info[u"title"]).upper()
    for kw in (keywords or []):
        if kw and kw.upper() in hay:
            return True
    return False


def _role_default(links, memory_uid, keywords):
    """(name_or_None, source_label) — remembered choice first, else a
    keyword auto-detect only if it's unambiguous."""
    if memory_uid:
        for li in links:
            try:
                if li[u"instance"].UniqueId == memory_uid:
                    return li[u"name"], u"remembered"
            except Exception:
                continue

    candidates = [li for li in links if _matches_any(li, keywords)]
    if len(candidates) == 1:
        return candidates[0][u"name"], u"auto-detected"
    if len(candidates) > 1:
        return None, u"{} keyword matches — pick one".format(len(candidates))
    return None, u"no keyword match"


def _role_default_multi(links, memory_uids, keywords):
    """(list_of_names, source_label) for a multi-select role (Architecture/
    Structure) — remembered set first (any UID that still resolves), else
    every keyword match is pre-checked (the user narrows down, not just
    picks one from an ambiguous set)."""
    if memory_uids:
        uid_set = set(memory_uids)
        names = []
        for li in links:
            try:
                if li[u"instance"].UniqueId in uid_set:
                    names.append(li[u"name"])
            except Exception:
                continue
        if names:
            return names, u"remembered"

    candidates = [li[u"name"] for li in links if _matches_any(li, keywords)]
    if candidates:
        return candidates, u"{} keyword match{}".format(
            len(candidates), u"" if len(candidates) == 1 else u"es")
    return [], u"no keyword match"


# ─────────────────────────────────────────────────────────────────────────────
# VIEW HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_view_and_validate():
    view = doc.ActiveView
    if view is None or view.IsTemplate or not isinstance(view, DB.ViewPlan):
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics",
            u"Open a 2D plan view (Floor Plan / Ceiling Plan / Area Plan) — not a "
            u"view template, 3D view, section, elevation or sheet — then run this "
            u"command again."
        )
        return None
    return view


def is_basement_view(view):
    name = _elem_name(view).upper()
    for tok in BASEMENT_TOKENS:
        if tok.upper() in name:
            return True
    try:
        level = view.GenLevel
        if level is not None:
            lname = _elem_name(level).upper()
            for tok in BASEMENT_TOKENS:
                if tok.upper() in lname:
                    return True
            if level.Elevation < 0:
                return True
    except Exception:
        pass
    return False


def _hide_category_safe(target, bic, warnings, hide=True):
    try:
        cat_id = DB.ElementId(bic)
        target.SetCategoryHidden(cat_id, hide)
    except Exception as ex:
        warnings.append(u"Could not {} category '{}': {}".format(
            u"hide" if hide else u"show", bic.ToString(), ex))


def _override_category_safe(target, bic, ogs, warnings):
    try:
        cat_id = DB.ElementId(bic)
        target.SetCategoryOverrides(cat_id, ogs)
    except Exception as ex:
        warnings.append(u"Could not set the fallback color for category '{}': {}".format(
            bic.ToString(), ex))


def _ensure_room_tags_visible(target, warnings):
    room_tags = getattr(DB.BuiltInCategory, "OST_RoomTags", None)
    if room_tags is None:
        return
    _hide_category_safe(target, room_tags, warnings, hide=False)


# ─────────────────────────────────────────────────────────────────────────────
# VIEW TEMPLATE  (Step 3)
# ─────────────────────────────────────────────────────────────────────────────

def _find_view_template_by_name(name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if v.IsTemplate and _elem_name(v) == name:
            return v
    return None


def _exclude_rvt_links_from_template_control(template, warnings):
    """Best-effort (see module docstring) — a failure here is informational
    only, it doesn't block anything else this tool does."""
    try:
        param_id = None
        for p in template.Parameters:
            if p.Definition is not None and p.Definition.Name == u"V/G Overrides RVT Links":
                param_id = p.Id
                break
        if param_id is None:
            warnings.append(
                u"Could not find the 'V/G Overrides RVT Links' template control row in "
                u"this Revit version — informational only, link visibility/halftone are "
                u"view-specific regardless of this setting.")
            return
        current_ids = list(template.GetNonControlledTemplateParameterIds())
        if not any(i.IntegerValue == param_id.IntegerValue for i in current_ids):
            current_ids.append(param_id)
            template.SetNonControlledTemplateParameterIds(SCG.List[DB.ElementId](current_ids))
    except Exception as ex:
        warnings.append(
            u"Could not exclude 'V/G Overrides RVT Links' from template control: {} "
            u"(informational only).".format(ex))


def ensure_view_template(view, host_clutter_bics, link_model_bics, wallnoncore_bics,
                          annotation_bics, override_bics, warnings):
    template = _find_view_template_by_name(TEMPLATE_NAME)
    if template is None:
        try:
            template_id = view.CreateViewTemplate()
            template = doc.GetElement(template_id)
            template.Name = TEMPLATE_NAME
        except Exception as ex:
            warnings.append(u"Could not create the '{}' view template: {}".format(
                TEMPLATE_NAME, ex))
            return None

    for bic in host_clutter_bics:
        _hide_category_safe(template, bic, warnings)
    for bic in link_model_bics:
        _hide_category_safe(template, bic, warnings)
    for bic in wallnoncore_bics:
        _hide_category_safe(template, bic, warnings)
    for bic in annotation_bics:
        _hide_category_safe(template, bic, warnings)

    _ensure_room_tags_visible(template, warnings)

    # Refinement 1 — Grids stay visible as a category (so the links' grids
    # show through); the HOST's own grids are hidden separately, per-element,
    # in run() — see module docstring for why this can't be a category hide.
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    if grids_bic is not None:
        _hide_category_safe(template, grids_bic, warnings, hide=False)

    gray_ogs = DB.OverrideGraphicSettings()
    try:
        gray_ogs.SetProjectionLineColor(GRAY_COLOR)
    except Exception as ex:
        warnings.append(u"Could not set the gray fallback projection color: {}".format(ex))
    for bic in override_bics:
        _override_category_safe(template, bic, gray_ogs, warnings)

    _exclude_rvt_links_from_template_control(template, warnings)

    try:
        view.ViewTemplateId = template.Id
    except Exception as ex:
        warnings.append(u"Could not apply the '{}' template to the active view: {}".format(
            TEMPLATE_NAME, ex))

    return template


# ─────────────────────────────────────────────────────────────────────────────
# DWG IMPORTS / TRAFFIC-LINK CATEGORY RESTRICTION  (element-level, view-specific)
# ─────────────────────────────────────────────────────────────────────────────

def _hide_dwg_imports_in_link(view, link_info, basement_view, warnings):
    link_doc = link_info[u"doc"]
    if link_doc is None:
        return
    try:
        imports = list(DB.FilteredElementCollector(link_doc).OfClass(DB.ImportInstance))
    except Exception as ex:
        warnings.append(u"Could not scan '{}' for imported DWG categories: {}".format(
            link_info[u"name"], ex))
        return
    if not imports:
        return

    to_hide = SCG.List[DB.ElementId]()
    for imp in imports:
        try:
            cat = imp.Category
            name = _elem_name(cat)
        except Exception:
            name = u""

        if basement_view and DWG_KEEP_TOKEN.upper() in (name or u"").upper():
            continue

        try:
            if imp.CanBeHidden(view):
                to_hide.Add(imp.Id)
        except Exception as ex:
            warnings.append(u"Could not evaluate DWG import '{}' in '{}': {}".format(
                name, link_info[u"name"], ex))

    if to_hide.Count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(
                u"Could not hide {} imported DWG object(s) inside '{}': {}".format(
                    to_hide.Count, link_info[u"name"], ex))


def _restrict_traffic_link_visibility(view, traffic_link, keep_bics, warnings):
    """Best-effort element-level hide (see module docstring — same class of
    cross-document uncertainty as the DWG-import hide above, same defensive
    handling)."""
    link_doc = traffic_link[u"doc"]
    if link_doc is None:
        warnings.append(u"Traffic link is unloaded — could not restrict its categories.")
        return

    try:
        categories = list(link_doc.Settings.Categories)
    except Exception as ex:
        warnings.append(u"Could not read categories from the Traffic link: {}".format(ex))
        return

    to_hide = SCG.List[DB.ElementId]()
    for cat in categories:
        try:
            bic = DB.BuiltInCategory(cat.Id.IntegerValue)
        except Exception:
            bic = None
        if bic in keep_bics:
            continue
        try:
            elems = (DB.FilteredElementCollector(link_doc)
                       .WherePasses(DB.ElementCategoryFilter(cat.Id))
                       .WhereElementIsNotElementType()
                       .ToElements())
        except Exception:
            continue
        for e in elems:
            try:
                if e.CanBeHidden(view):
                    to_hide.Add(e.Id)
            except Exception:
                pass

    if to_hide.Count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(
                u"Could not hide {} element(s) inside the Traffic link: {} "
                u"(View.HideElements may not support elements from a linked "
                u"document in this Revit version).".format(to_hide.Count, ex))


def _set_traffic_halftone(view, traffic_link, warnings):
    ogs = DB.OverrideGraphicSettings()
    try:
        ogs.SetHalftone(True)
    except Exception as ex:
        warnings.append(u"Could not set the Traffic link to halftone: {}".format(ex))
        return
    try:
        view.SetElementOverrides(traffic_link[u"id"], ogs)
    except Exception as ex:
        warnings.append(u"Could not apply halftone to the Traffic link instance: {}".format(ex))


# ─────────────────────────────────────────────────────────────────────────────
# GRIDS  (Refinement 1)
# ─────────────────────────────────────────────────────────────────────────────

def _hide_host_grids(view, warnings):
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    if grids_bic is None:
        return
    try:
        host_grids = (DB.FilteredElementCollector(doc)
                        .OfCategory(grids_bic)
                        .WhereElementIsNotElementType()
                        .ToElements())
    except Exception as ex:
        warnings.append(u"Could not collect host Grids: {}".format(ex))
        return

    to_hide = SCG.List[DB.ElementId]()
    for g in host_grids:
        try:
            if g.CanBeHidden(view) and not g.IsHidden(view):
                to_hide.Add(g.Id)
        except Exception:
            pass

    if to_hide.Count:
        try:
            view.HideElements(to_hide)
        except Exception as ex:
            warnings.append(u"Could not hide {} host Grid(s): {}".format(to_hide.Count, ex))


def _color_link_grids(view, link_info, ogs, warnings, label):
    grids_bic = getattr(DB.BuiltInCategory, "OST_Grids", None)
    link_doc = link_info[u"doc"]
    if grids_bic is None or link_doc is None:
        return
    try:
        grids = (DB.FilteredElementCollector(link_doc)
                   .OfCategory(grids_bic)
                   .WhereElementIsNotElementType()
                   .ToElements())
    except Exception:
        return

    total  = 0
    failed = 0
    for g in grids:
        total += 1
        try:
            view.SetElementOverrides(g.Id, ogs)
        except Exception:
            failed += 1

    if total == 0:
        return
    if failed == total:
        warnings.append(
            u"Could not color any Grid inside '{}' — Revit rejected every attempt "
            u"(View.SetElementOverrides may not support elements from a linked "
            u"document in this Revit version).".format(link_info[u"name"]))
    elif failed:
        warnings.append(u"{} of {} Grids inside '{}' could not be colored.".format(
            failed, total, link_info[u"name"]))


# ─────────────────────────────────────────────────────────────────────────────
# DEEP-SCAN CONCRETE CLASSIFICATION  (Step 8B)
# ─────────────────────────────────────────────────────────────────────────────

def _bip_text(elem, bip_name):
    bip = getattr(DB.BuiltInParameter, bip_name, None)
    if bip is None:
        return u""
    try:
        p = elem.get_Parameter(bip)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def _named_text(elem, display_name):
    try:
        p = elem.LookupParameter(display_name)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def _first_text(elem, bip_name, display_name):
    return _bip_text(elem, bip_name) or _named_text(elem, display_name)


def _contains_any(haystack, keywords):
    up = (haystack or u"").upper()
    return any((k or u"").upper() in up for k in (keywords or []) if k)


def _material_name(link_doc, material_id):
    if material_id is None or material_id == DB.ElementId.InvalidElementId:
        return u""
    try:
        mat = link_doc.GetElement(material_id)
        return _elem_name(mat)
    except Exception:
        return u""


def _wall_core_material_names(link_doc, wall_type):
    names = []
    try:
        cs = wall_type.GetCompoundStructure()
        if cs is None:
            return names
        first = cs.GetFirstCoreLayerIndex()
        last  = cs.GetLastCoreLayerIndex()
        layers = list(cs.GetLayers())
        for i, layer in enumerate(layers):
            if first <= i <= last:
                nm = _material_name(link_doc, layer.MaterialId)
                if nm:
                    names.append(nm)
    except Exception:
        pass
    return names


def _structural_material_name(link_doc, elem_type):
    bip = getattr(DB.BuiltInParameter, "STRUCTURAL_MATERIAL_PARAM", None)
    if bip is None:
        return u""
    try:
        p = elem_type.get_Parameter(bip)
        if p is not None:
            return _material_name(link_doc, p.AsElementId())
    except Exception:
        pass
    return u""


def _type_is_concrete(link_doc, elem_type, bic, cfg):
    concrete_kw = cfg.get(u"ConcreteKeywords") or []
    exclude_kw  = cfg.get(u"ExcludeKeywords") or []

    texts = [_elem_name(elem_type)]
    texts.append(_first_text(elem_type, "ALL_MODEL_DESCRIPTION", u"Description"))
    texts.append(_first_text(elem_type, "ALL_MODEL_TYPE_COMMENTS", u"Type Comments"))

    if bic == DB.BuiltInCategory.OST_Walls:
        texts.extend(_wall_core_material_names(link_doc, elem_type))
    else:
        mat = _structural_material_name(link_doc, elem_type)
        if mat:
            texts.append(mat)

    combined = u" | ".join(t for t in texts if t)
    if not _contains_any(combined, concrete_kw):
        return False
    if _contains_any(combined, exclude_kw):
        return False
    return True


def _collect_concrete_type_names(link_doc, categories, cfg, warnings, label):
    """Iterate INSTANCES (not just types) because Wall Structural Usage
    (Bearing/Shear) is an instance property — a type only counts if at least
    one qualifying instance uses it."""
    names = set()
    type_cache = {}  # type_id (int) -> (is_concrete, type_name)

    if link_doc is None:
        return names

    for bic in categories:
        try:
            elems = (DB.FilteredElementCollector(link_doc)
                       .OfCategory(bic)
                       .WhereElementIsNotElementType()
                       .ToElements())
        except Exception:
            continue

        for elem in elems:
            if bic == DB.BuiltInCategory.OST_Walls:
                try:
                    usage = elem.StructuralUsage
                except Exception:
                    usage = None
                if usage not in (Structure.StructuralWallUsage.Bearing,
                                  Structure.StructuralWallUsage.Shear):
                    continue

            try:
                type_id = elem.GetTypeId()
            except Exception:
                continue
            if type_id is None or type_id == DB.ElementId.InvalidElementId:
                continue

            key = type_id.IntegerValue
            if key not in type_cache:
                elem_type = link_doc.GetElement(type_id)
                is_conc = False
                type_name = None
                if elem_type is not None:
                    type_name = _elem_name(elem_type)
                    try:
                        is_conc = _type_is_concrete(link_doc, elem_type, bic, cfg)
                    except Exception as ex:
                        warnings.append(u"{}: could not classify type '{}': {}".format(
                            label, type_name, ex))
                type_cache[key] = (is_conc, type_name)

            is_conc, type_name = type_cache[key]
            if is_conc and type_name:
                names.add(type_name)

    return names


# ─────────────────────────────────────────────────────────────────────────────
# VIEW FILTERS  (Step 8C/8D)
# ─────────────────────────────────────────────────────────────────────────────

def _find_existing_filter(name):
    for f in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement):
        if _elem_name(f) == name:
            return f
    return None


def build_or_update_type_name_filter(filter_name, category_bics, type_names, warnings):
    if not type_names:
        warnings.append(u"No concrete type names detected for '{}' — filter left "
                         u"unchanged.".format(filter_name))
        return _find_existing_filter(filter_name)

    cat_ids = SCG.List[DB.ElementId]([DB.ElementId(b) for b in category_bics])

    bip = getattr(DB.BuiltInParameter, "ALL_MODEL_TYPE_NAME", None)
    if bip is None:
        warnings.append(u"ALL_MODEL_TYPE_NAME is not available in this Revit version — "
                         u"could not build filter '{}'.".format(filter_name))
        return None
    provider = DB.ParameterValueProvider(DB.ElementId(bip))

    rules = []
    for name in sorted(type_names):
        try:
            rule = DB.FilterStringRule(provider, DB.FilterStringEquals(), name)
            rules.append(DB.ElementParameterFilter(rule))
        except Exception as ex:
            warnings.append(u"Could not build a filter rule for type '{}': {}".format(name, ex))

    if not rules:
        warnings.append(u"No usable filter rules for '{}'.".format(filter_name))
        return None

    combined = rules[0] if len(rules) == 1 else DB.LogicalOrFilter(SCG.List[DB.ElementFilter](rules))

    pfe = _find_existing_filter(filter_name)
    try:
        if pfe is None:
            pfe = DB.ParameterFilterElement.Create(doc, filter_name, cat_ids)
        else:
            pfe.SetCategories(cat_ids)
        pfe.SetElementFilter(combined)
    except Exception as ex:
        warnings.append(u"Could not create/update view filter '{}': {}".format(filter_name, ex))
        return None

    return pfe


def apply_filter_to_target(target, pfe, ogs, warnings, label):
    if pfe is None:
        return
    try:
        applied_ids = [i.IntegerValue for i in target.GetFilters()]
        if pfe.Id.IntegerValue not in applied_ids:
            target.AddFilter(pfe.Id)
        target.SetFilterOverrides(pfe.Id, ogs)
    except Exception as ex:
        warnings.append(u"Could not apply the {} view filter: {}".format(label, ex))


# ─────────────────────────────────────────────────────────────────────────────
# GRAPHIC OVERRIDES  (Step 8D)
# ─────────────────────────────────────────────────────────────────────────────

def get_solid_line_pattern_id():
    try:
        return DB.LinePatternElement.GetSolidPatternId()
    except Exception:
        return DB.ElementId.InvalidElementId


def _fuzzy_tokens_from_pattern_name(name):
    base = (name or u"").split(u"-")[0]
    words = [w.upper() for w in base.replace(u",", u" ").split() if w.isalpha()]
    return words or [u"DIAGONAL"]


def find_fill_pattern_id(exact_name, warnings, label):
    """Exact drafting-pattern name match, else a drafting pattern containing
    every word from the configured name, else any drafting pattern with
    'DIAGONAL' in its name, else InvalidElementId (+ a warning)."""
    fuzzy_tokens = _fuzzy_tokens_from_pattern_name(exact_name)

    drafting = []
    for fpe in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        try:
            fp = fpe.GetFillPattern()
            if fp is not None and fp.Target == DB.FillPatternTarget.Drafting:
                drafting.append(fpe)
        except Exception:
            continue

    for fpe in drafting:
        if _elem_name(fpe) == exact_name:
            return fpe.Id

    for fpe in drafting:
        nm = _elem_name(fpe).upper()
        if all(tok in nm for tok in fuzzy_tokens):
            return fpe.Id

    for fpe in drafting:
        if u"DIAGONAL" in _elem_name(fpe).upper():
            return fpe.Id

    warnings.append(
        u"No fill pattern named '{}' (and no diagonal fallback) was found for the "
        u"{} cut background pattern — that override was skipped.".format(exact_name, label))
    return DB.ElementId.InvalidElementId


def _settings_color(settings, key, fallback):
    c = settings.get(key) or {}
    try:
        return DB.Color(int(c.get(u"R", 0)), int(c.get(u"G", 0)), int(c.get(u"B", 0)))
    except Exception:
        return fallback


def build_colored_override(color, pattern_name, warnings, label):
    ogs = DB.OverrideGraphicSettings()

    # Grids have no "cut" representation (they're a projection-only datum
    # category), so the cut-pattern overrides below have no visible effect on
    # them — this is what actually colors a grid line.
    try:
        ogs.SetProjectionLineColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the projection line color: {}".format(label, ex))

    try:
        ogs.SetSurfaceTransparency(100)
    except Exception as ex:
        warnings.append(u"{}: could not set surface transparency: {}".format(label, ex))

    try:
        ogs.SetCutLineWeight(1)
    except Exception as ex:
        warnings.append(u"{}: could not set cut line weight: {}".format(label, ex))

    solid_id = get_solid_line_pattern_id()
    if solid_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutLinePatternId(solid_id)
        except Exception as ex:
            warnings.append(u"{}: could not set cut line pattern: {}".format(label, ex))

    try:
        ogs.SetCutForegroundPatternVisible(False)
    except Exception as ex:
        warnings.append(u"{}: could not disable the cut foreground pattern: {}".format(label, ex))

    try:
        ogs.SetCutBackgroundPatternVisible(True)
    except Exception as ex:
        warnings.append(u"{}: could not enable the cut background pattern: {}".format(label, ex))

    try:
        ogs.SetCutLineColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the cut line color: {}".format(label, ex))

    fill_id = find_fill_pattern_id(pattern_name, warnings, label)
    if fill_id != DB.ElementId.InvalidElementId:
        try:
            ogs.SetCutBackgroundPatternId(fill_id)
        except Exception as ex:
            warnings.append(u"{}: could not set the cut background pattern id: {}".format(label, ex))

    try:
        ogs.SetCutBackgroundPatternColor(color)
    except Exception as ex:
        warnings.append(u"{}: could not set the cut background pattern color: {}".format(label, ex))

    return ogs


# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATIC SHEET CREATION  (Refinement 2)
# ─────────────────────────────────────────────────────────────────────────────

def find_title_block_symbol(warnings):
    """Exact-token match on Family Name or Type Name first; else a manual
    forms.SelectFromList pick among every loaded title block type."""
    try:
        symbols = list(DB.FilteredElementCollector(doc)
                          .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                          .WhereElementIsElementType()
                          .ToElements())
    except Exception as ex:
        warnings.append(u"Could not collect title block types: {}".format(ex))
        return None

    if not symbols:
        warnings.append(u"No title block family types are loaded in this project — "
                         u"sheet creation was skipped.")
        return None

    def _fam_name(sym):
        try:
            return _elem_name(sym.Family)
        except Exception:
            return u""

    token = TITLEBLOCK_TOKEN.upper()
    for sym in symbols:
        try:
            if token in _fam_name(sym).upper() or token in _elem_name(sym).upper():
                return sym
        except Exception:
            continue

    label_map = {}
    for sym in symbols:
        try:
            label = u"{} : {}".format(_fam_name(sym), _elem_name(sym))
            label_map[label] = sym
        except Exception:
            continue
    labels = sorted(label_map.keys())
    if not labels:
        warnings.append(u"Could not read any title block type's name — sheet creation was skipped.")
        return None

    picked = ebui.pick_from_list(
        labels, title=u"Select Title Block",
        prompt=u"'{}' was not found — pick a title block for the coordination "
               u"sheet.".format(TITLEBLOCK_TOKEN))
    if not picked:
        warnings.append(u"No title block selected — sheet creation was skipped.")
        return None
    return label_map.get(picked)


def _view_is_on_a_sheet(view):
    try:
        for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
            if vp.ViewId == view.Id:
                return True
    except Exception:
        pass
    return False


def _next_sheet_number(warnings):
    best = 0
    try:
        for sh in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            num = sh.SheetNumber or u""
            if num.upper().startswith(SHEET_NUMBER_PREFIX.upper()):
                m = re.match(r"^\D*(\d+)", num[len(SHEET_NUMBER_PREFIX):])
                if m:
                    try:
                        val = int(m.group(1))
                        if val > best:
                            best = val
                    except Exception:
                        continue
    except Exception as ex:
        warnings.append(u"Could not scan existing sheet numbers: {}".format(ex))
    return u"{}{:03d}".format(SHEET_NUMBER_PREFIX, best + 1)


def _get_view_for_sheet(view, template, warnings):
    """Step: safety check. A view can only be placed on one sheet — if the
    active view is already on one, duplicate it (WithDetailing carries over
    the element-level hides/overrides already applied) and use the copy."""
    if not _view_is_on_a_sheet(view):
        return view
    try:
        new_view_id = view.Duplicate(DB.ViewDuplicateOption.WithDetailing)
        new_view = doc.GetElement(new_view_id)
    except Exception as ex:
        warnings.append(u"The active view is already on a sheet and could not be "
                         u"duplicated for a new one: {}".format(ex))
        return None
    try:
        new_view.ViewTemplateId = template.Id
    except Exception as ex:
        warnings.append(u"Could not apply the '{}' template to the duplicated view: {}".format(
            TEMPLATE_NAME, ex))
    return new_view


def _create_and_place_sheet(view_for_sheet, view_name, titleblock_symbol, warnings):
    try:
        if not titleblock_symbol.IsActive:
            titleblock_symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass

    try:
        sheet = DB.ViewSheet.Create(doc, titleblock_symbol.Id)
    except Exception as ex:
        warnings.append(u"Could not create the coordination sheet: {}".format(ex))
        return None

    number = _next_sheet_number(warnings)
    try:
        sheet.SheetNumber = number
    except Exception as ex:
        warnings.append(u"Could not set the sheet number to '{}': {}".format(number, ex))
    try:
        sheet.Name = view_name
    except Exception as ex:
        warnings.append(u"Could not set the sheet name to '{}': {}".format(view_name, ex))

    try:
        viewport = DB.Viewport.Create(doc, sheet.Id, view_for_sheet.Id, DB.XYZ.Zero)
    except Exception as ex:
        warnings.append(u"Could not place the view on the sheet: {}".format(ex))
        return sheet

    try:
        tb_inst = (DB.FilteredElementCollector(doc, sheet.Id)
                     .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                     .WhereElementIsNotElementType()
                     .FirstElement())
        if tb_inst is not None:
            bbox = tb_inst.get_BoundingBox(sheet)
            if bbox is not None:
                center = DB.XYZ((bbox.Min.X + bbox.Max.X) / 2.0,
                                 (bbox.Min.Y + bbox.Max.Y) / 2.0, 0)
                viewport.SetBoxCenter(center)
    except Exception as ex:
        warnings.append(u"Could not center the viewport on the title block: {}".format(ex))

    return sheet


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run():
    view = get_view_and_validate()
    if view is None:
        return

    settings = cfgmod.load_settings()

    links = get_all_link_instances()
    if not links:
        TaskDialog.Show(u"EasyBIM — Coordination Graphics",
                         u"No linked Revit models were found in this project.")
        return

    memory = cfgmod.load_link_memory(doc)
    # Back-compat: older memory files (before multi-select) stored a single
    # 'arch_uid'/'struct_uid' — treat that as a one-item list if the new
    # plural keys aren't there yet.
    arch_memory_uids = memory.get(u"arch_uids") or ([memory[u"arch_uid"]] if memory.get(u"arch_uid") else [])
    struct_memory_uids = memory.get(u"struct_uids") or ([memory[u"struct_uid"]] if memory.get(u"struct_uid") else [])

    arch_default    = _role_default_multi(links, arch_memory_uids, settings.get(u"ArchLinkKeywords"))
    struct_default  = _role_default_multi(links, struct_memory_uids, settings.get(u"StructLinkKeywords"))
    traffic_default = _role_default(links, memory.get(u"traffic_uid"), settings.get(u"TrafficLinkKeywords"))
    use_traffic_default = bool(memory.get(u"use_traffic")) and traffic_default[0] is not None

    try:
        level_name = _elem_name(view.GenLevel) or u"—"
    except Exception:
        level_name = u"—"
    view_info = {
        u"name"     : _elem_name(view),
        u"view_type": view.ViewType.ToString(),
        u"level"    : level_name,
        u"basement" : is_basement_view(view),
    }

    dlg = LinkPickerDialog(links, {
        u"arch": arch_default, u"struct": struct_default, u"traffic": traffic_default,
        u"use_traffic": use_traffic_default,
    }, view_info)
    dlg.show()
    if dlg.cancelled:
        return

    arch_links, struct_links = dlg.arch_links, dlg.struct_links
    traffic_link, use_traffic = dlg.traffic_link, dlg.use_traffic

    # Settings may have been edited mid-wizard via the gear icon — reload so
    # the transaction below (colors, patterns, concrete/exclude keywords)
    # uses whatever is currently saved, not the snapshot from before dlg.show().
    settings = cfgmod.load_settings()

    cfgmod.save_link_memory(doc, {
        u"arch_uids"  : [li[u"instance"].UniqueId for li in arch_links],
        u"struct_uids": [li[u"instance"].UniqueId for li in struct_links],
        u"traffic_uid": traffic_link[u"instance"].UniqueId if traffic_link else memory.get(u"traffic_uid"),
        u"use_traffic": use_traffic,
    })

    warnings = []
    chosen_ids = set(li[u"id"].IntegerValue for li in arch_links)
    chosen_ids |= set(li[u"id"].IntegerValue for li in struct_links)
    if use_traffic and traffic_link:
        chosen_ids.add(traffic_link[u"id"].IntegerValue)

    # Resolved before the transaction (like the link picker) since it may
    # show a forms.SelectFromList prompt. None just means sheet creation is
    # skipped later — it never blocks the graphics/template/filter work.
    titleblock_symbol = find_title_block_symbol(warnings)

    t = DB.Transaction(doc, u"EasyBIM: Coordination Graphics")
    t.Start()
    try:
        host_clutter_bics = _resolve_categories(HOST_CLUTTER_CATEGORY_NAMES, warnings)
        link_model_bics   = _resolve_categories(LINK_MODEL_HIDE_CATEGORY_NAMES, warnings)
        wallnoncore_bics  = _resolve_categories([WALL_NONCORE_CATEGORY_NAME], warnings)
        annotation_bics   = _resolve_categories(LINK_ANNOTATION_HIDE_CATEGORY_NAMES, warnings)
        override_bics     = _resolve_categories(OVERRIDE_CATEGORY_NAMES, warnings)
        traffic_keep_bics = set(_resolve_categories(TRAFFIC_KEEP_CATEGORY_NAMES, warnings))

        # ── Step 3: shared view template (host clutter + Step 6 categories) ────
        template = ensure_view_template(view, host_clutter_bics, link_model_bics,
                                         wallnoncore_bics, annotation_bics,
                                         override_bics, warnings)
        if template is None:
            raise Exception(u"Could not set up the '{}' view template.".format(TEMPLATE_NAME))

        # ── Step 5: hide every other link ──────────────────────────────────────
        other_link_ids = SCG.List[DB.ElementId]()
        for li in links:
            if li[u"id"].IntegerValue in chosen_ids:
                continue
            try:
                if li[u"instance"].CanBeHidden(view) and not li[u"instance"].IsHidden(view):
                    other_link_ids.Add(li[u"id"])
            except Exception as ex:
                warnings.append(u"Could not hide link '{}': {}".format(li[u"name"], ex))
        if other_link_ids.Count:
            try:
                view.HideElements(other_link_ids)
            except Exception as ex:
                warnings.append(u"Could not hide {} other link(s): {}".format(
                    other_link_ids.Count, ex))

        # ── Refinement 1: host Grids hidden (element-level — see docstring) ────
        _hide_host_grids(view, warnings)

        # ── Step 6 (DWGs): hide imported DWG categories inside Arch/Struct ─────
        basement = is_basement_view(view)
        for li in arch_links:
            _hide_dwg_imports_in_link(view, li, basement, warnings)
        for li in struct_links:
            _hide_dwg_imports_in_link(view, li, basement, warnings)

        # ── Step 7: Traffic link ────────────────────────────────────────────────
        if use_traffic and traffic_link:
            _restrict_traffic_link_visibility(view, traffic_link, traffic_keep_bics, warnings)
            _set_traffic_halftone(view, traffic_link, warnings)

        # ── Step 8: deep-scan (every selected link, per role) + View Filters ───
        arch_names = set()
        for li in arch_links:
            arch_names |= _collect_concrete_type_names(
                li[u"doc"], override_bics, settings, warnings,
                u"Architecture ({})".format(li[u"name"]))
        struct_names = set()
        for li in struct_links:
            struct_names |= _collect_concrete_type_names(
                li[u"doc"], override_bics, settings, warnings,
                u"Structure ({})".format(li[u"name"]))

        struct_pfe = build_or_update_type_name_filter(FILTER_NAME_STRUCT, override_bics,
                                                        struct_names, warnings)
        arch_pfe   = build_or_update_type_name_filter(FILTER_NAME_ARCH, override_bics,
                                                        arch_names, warnings)

        struct_color = _settings_color(settings, u"StructColor", DB.Color(200, 30, 30))
        arch_color   = _settings_color(settings, u"ArchColor", DB.Color(0, 70, 200))

        struct_ogs = build_colored_override(struct_color, settings.get(u"StructPatternName"),
                                             warnings, u"Structure")
        arch_ogs   = build_colored_override(arch_color, settings.get(u"ArchPatternName"),
                                             warnings, u"Architecture")

        apply_filter_to_target(template, struct_pfe, struct_ogs, warnings, u"Structure")
        apply_filter_to_target(template, arch_pfe, arch_ogs, warnings, u"Architecture")

        # ── Refinement 1: color Grids inside every selected Arch/Struct link ───
        for li in struct_links:
            _color_link_grids(view, li, struct_ogs, warnings, u"Structure")
        for li in arch_links:
            _color_link_grids(view, li, arch_ogs, warnings, u"Architecture")

        # ── Refinement 2: automatic sheet creation ──────────────────────────────
        sheet = None
        if titleblock_symbol is not None:
            view_for_sheet = _get_view_for_sheet(view, template, warnings)
            if view_for_sheet is not None:
                sheet = _create_and_place_sheet(view_for_sheet, _elem_name(view_for_sheet),
                                                 titleblock_symbol, warnings)

        t.Commit()
    except Exception:
        t.RollBack()
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics — Error",
            u"Coordination Graphics failed and no changes were made:\n\n{}".format(
                traceback.format_exc())
        )
        return

    if sheet is not None:
        try:
            uidoc.ActiveView = sheet
        except Exception as ex:
            warnings.append(u"Sheet created but could not switch to it: {}".format(ex))

    sheet_line = (u"Sheet: {} — {}".format(sheet.SheetNumber, _elem_name(sheet))
                  if sheet is not None else u"Sheet: not created (see warnings)")

    summary = (
        u"Coordination graphics applied.\n\n"
        u"Architecture link(s): {}\nStructure link(s): {}\nTraffic link: {}\n\n"
        u"Architecture concrete types found: {}\nStructure concrete types found: {}\n\n"
        u"{}".format(
            u", ".join(li[u"name"] for li in arch_links),
            u", ".join(li[u"name"] for li in struct_links),
            traffic_link[u"name"] if (use_traffic and traffic_link) else u"(not used)",
            len(arch_names), len(struct_names), sheet_line)
    )
    if warnings:
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics — Done, with warnings",
            u"{}\n\n{} warning(s):\n{}".format(
                summary, len(warnings), u"\n".join(u"• {}".format(w) for w in warnings))
        )
    else:
        TaskDialog.Show(u"EasyBIM — Coordination Graphics — Done", summary)


def main():
    try:
        run()
    except Exception:
        TaskDialog.Show(
            u"EasyBIM — Coordination Graphics — Error",
            u"Unexpected error:\n\n{}".format(traceback.format_exc())
        )


main()
