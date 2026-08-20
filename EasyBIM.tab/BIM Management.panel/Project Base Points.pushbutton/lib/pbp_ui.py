# -*- coding: utf-8 -*-
"""Project Base Points — the wizard dialog: Setup -> Results -> Export format
-> ACC issue fields (only if Excel/xlsx is picked) -> Done. Screen-by-screen
spec: docs/handoffs/project_base_points/.../README.md; visual/interaction
reference: reference/ribbon-base-points.jsx + reference/ribbon-base-points-
export.jsx.

Same construction pattern as every other multi-stage EasyBIM tool (Solution
Section / Head Height Check): one Window, its XAML is a literal string loaded
via XamlReader, a plain Python controller class finds named elements and
wires `.Click +=` etc. by hand (no MVVM/code-behind compilation). State lives
as plain attributes on the controller.

*** FIRST USE OF WPF DataGrid IN THIS CODEBASE ***
Every existing tool hand-builds its lists as Borders/StackPanels in Python
(fine for short lists). The Results table (12 columns, sortable, filterable,
per-cell editable, tens of rows) and the ACC Preview grid are built as WPF
DataGrid bound to a System.Data.DataTable/DataView instead — that gets
click-to-sort and RowFilter-based filtering for free from WPF/ADO.NET, at the
cost of being a new pattern here. Editable cells (Disc / Reference (AR) /
Included, and the Preview grid's Discipline) are plain two-way data-bound
DataTemplate columns; a single DataTable.RowChanged handler (not a per-cell
event) recomputes status and refreshes derived columns after any edit.

*** SCOPE SIMPLIFICATIONS (surfaced to the requester, not silent cuts) ***
- Toggle switches are plain WPF CheckBoxes, not the pill-shaped switches in
  the design reference (README fidelity note: match structure, not CSS).
- The Reference (AR) / Disc combo cells for rows that don't apply (HOST, AR
  rows themselves) are shown disabled/greyed rather than swapped for plain
  text — same information, simpler binding.
- The Preview grid's Start/Due Date columns are plain editable text (ISO
  format) rather than native WPF DatePickers.
- The Preview grid's multi-row "select + bulk edit" affordance from the
  design reference is NOT implemented in this first version — every cell is
  still individually editable. Flagged for a follow-up if it's wanted.
"""
import clr
import datetime
import os
import re
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System')
clr.AddReference('System.Data')
clr.AddReference('System.Windows.Forms')

import System
import System.Windows
import System.Windows.Controls as WC
import System.Windows.Media as WM
import System.Data as SD
from System.Collections.Generic import List as NetList

from System.Windows.Markup import XamlReader
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader
from System.Windows.Threading import Dispatcher, DispatcherFrame, DispatcherTimer

from pyrevit import script

import pbp_prefs
import pbp_report_html
import pbp_report_xlsx
from pbp_disc_map import DISC_CODES, HUB_ROLES, HEB_OPTIONS, AR_CODES, auto_role, auto_heb, guess_role
from pbp_match import auto_match_references, resolve_rows, disc_of, key_of, ar_option_labels, is_ar
from pbp_status import ISSUE_STATUSES

logger = script.get_logger()

NAVY, CYAN = "#1e248c", "#44b8d3"
OK_C, ORANGE, RED, MUTE = "#22b07c", "#e0851e", "#d64545", "#8b93a7"
TONE = {"OK": OK_C, "Not OK": ORANGE, "Not Shared": RED, "Missing Ref": MUTE,
        "Reference": CYAN, "Unloaded": MUTE, "Host": MUTE}
BG = {"OK": "#E3F7EE", "Not OK": "#FCEEDD", "Not Shared": "#FBE6E6", "Missing Ref": "#EEF0F6",
      "Reference": "#E6F6FA", "Unloaded": "#F1F2F7", "Host": "#F1F2F7"}

# Every EasyBIM Hub project is guaranteed to carry these two custom fields on
# its ACC issue-import template -- confirmed with the requester (2026-08-10).
# When "Not in EasyBIM Hub" is unchecked, use these automatically instead of
# asking the None/Manual/Import custom-fields question at all.
HUB_FIELDS = [(u"Discipline", "discipline"), (u"EAB_Location", "building")]

FORMATS = [
    ("html", "Interactive HTML", ".html", "Sortable, filterable report with the coordination chart. Opens in the browser."),
    ("pdf", "Print-ready HTML (Ctrl+P to save as PDF)", "_print.html",
     "Print layout of the chart and table as filtered here -- pick Landscape yourself in the print dialog "
     "(the page tells you to). No PDF library exists in this codebase yet (see lib/pbp_report_html.py) so this "
     "opens a print-ready page instead of writing a .pdf directly."),
    ("xlsx", "Excel - ACC issue import", ".xlsx", "One row per flagged link on ACC's issue-import template."),
]

_SORT_ARROWS = {1: " ▲", -1: " ▼"}


def _sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name or "model")


def _fmt(v, nd=3):
    return u"—" if v is None else (u"{:.%df}" % nd).format(v)


_REASON_LABEL = {"elev": u"Elev", "angle": u"Angle", "both": u"Both"}


def _status_display(status, reason):
    """"Not OK" alone doesn't say why -- append which criterion(s) failed
    (elevation, angle, or both; plan position never drives status, see
    pbp_status.status_of)."""
    if status == "Not OK" and reason in _REASON_LABEL:
        return u"{} · {}".format(status, _REASON_LABEL[reason])
    return status


# ─────────────────────────────────────────────────────────────────────────────
# XAML — window chrome + the four scrolling stages; Results is built entirely
# in Python into ResultsHost since it needs a DataGrid, not a ScrollViewer.
# ─────────────────────────────────────────────────────────────────────────────
XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Project Base Points"
  Width="1040" Height="760" MinWidth="860" MinHeight="560"
  WindowStartupLocation="CenterScreen"
  ResizeMode="CanResize"
  WindowStyle="SingleBorderWindow"
  FontFamily="Segoe UI"
  Background="#f7f8ff">
  <Window.Resources>
    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background" Value="#1e248c"/>
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Height" Value="34"/>
      <Setter Property="Padding" Value="16,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" CornerRadius="7" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter Property="Background" Value="#44b8d3"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.42"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="Foreground" Value="#6b7280"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Height" Value="34"/>
      <Setter Property="Padding" Value="12,0"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush" Value="#e8eaff"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="7" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter Property="Background" Value="#f0f2ff"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="CardBorder" TargetType="Border">
      <Setter Property="Background" Value="White"/>
      <Setter Property="BorderBrush" Value="#e6e8f5"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius" Value="10"/>
    </Style>
    <Style x:Key="SectionLabel" TargetType="TextBlock">
      <Setter Property="FontFamily" Value="Consolas"/>
      <Setter Property="FontSize" Value="10.5"/>
      <Setter Property="Foreground" Value="#9aa0ac"/>
      <Setter Property="Margin" Value="2,0,0,6"/>
    </Style>
  </Window.Resources>

  <Grid x:Name="RootGrid">
    <Grid.RowDefinitions>
      <RowDefinition Height="70"/>
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
          <ColumnDefinition Width="32"/>
        </Grid.ColumnDefinitions>
        <Border Grid.Column="0" Width="40" Height="40" CornerRadius="10" VerticalAlignment="Center" Background="#151b6e">
          <Path Data="M12,3 A9,9 0 1 0 12.01,3 Z M5.6,5.6 L18.4,18.4 M18.4,5.6 L5.6,18.4"
                Stroke="White" StrokeThickness="1.6" StrokeLineJoin="Round"
                StrokeStartLineCap="Round" StrokeEndLineCap="Round"
                Width="22" Height="22" Stretch="Uniform"
                HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="Project Base Points" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock x:Name="SubtitleTB" Text="Host model &amp; all links &#183; coordination audit"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <StackPanel Grid.Column="2" Orientation="Horizontal" VerticalAlignment="Center" Margin="0,0,10,0">
          <Button x:Name="ZoomOutBtn" Content="A-" Width="30" Height="26" Margin="0,0,4,0"
                  Background="#151b6e" Foreground="White" BorderThickness="0"
                  FontSize="11" FontWeight="Bold" Cursor="Hand" ToolTip="Smaller text (Ctrl+scroll down)"/>
          <Button x:Name="ZoomInBtn" Content="A+" Width="30" Height="26"
                  Background="#151b6e" Foreground="White" BorderThickness="0"
                  FontSize="11" FontWeight="Bold" Cursor="Hand" ToolTip="Bigger text (Ctrl+scroll up)"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="3" Content="&#10005;" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- BANNER -->
    <Border x:Name="BannerBorder" Grid.Row="1" Background="#ecf8fc" BorderBrush="#bbe5f0"
            BorderThickness="0,0,0,1" Padding="20,9">
      <TextBlock x:Name="BannerText" FontSize="12" Foreground="#1e6e87" TextWrapping="Wrap"/>
    </Border>

    <!-- BODY: one Grid cell per stage, only one Visible at a time -->
    <Grid Grid.Row="2" Margin="20,12,20,10">

      <ScrollViewer x:Name="SetupScroll" VerticalScrollBarVisibility="Auto">
        <StackPanel x:Name="SetupPanel"/>
      </ScrollViewer>

      <Grid x:Name="ResultsHost" Visibility="Collapsed"/>

      <ScrollViewer x:Name="ArConfigScroll" VerticalScrollBarVisibility="Auto" Visibility="Collapsed">
        <StackPanel x:Name="ArConfigPanel"/>
      </ScrollViewer>

      <ScrollViewer x:Name="ExportScroll" VerticalScrollBarVisibility="Auto" Visibility="Collapsed">
        <StackPanel x:Name="ExportPanel"/>
      </ScrollViewer>

      <ScrollViewer x:Name="IssuesScroll" VerticalScrollBarVisibility="Auto" Visibility="Collapsed">
        <StackPanel x:Name="IssuesPanel"/>
      </ScrollViewer>

      <ScrollViewer x:Name="DoneScroll" VerticalScrollBarVisibility="Auto" Visibility="Collapsed">
        <StackPanel x:Name="DonePanel"/>
      </ScrollViewer>

    </Grid>

    <!-- FOOTER -->
    <Border Grid.Row="3" BorderBrush="#e6e8f5" BorderThickness="0,1,0,0" Background="#f8f9ffB0" Padding="18,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="FooterStatusTB" Grid.Column="0" FontFamily="Consolas" FontSize="11.5"
                   Foreground="#6b7280" VerticalAlignment="Center" TextWrapping="Wrap"/>
        <StackPanel x:Name="FooterButtons" Grid.Column="1" Orientation="Horizontal"/>
      </Grid>
    </Border>
  </Grid>
</Window>
"""

# DataTemplate fragments for the two templated DataGrid columns (Disc, Reference)
# on the Results grid, and the Discipline column on the Preview grid. Parsed
# separately via XamlReader.Parse and assigned to column.CellTemplate; the
# DynamicResource lookups below resolve once the cell is actually placed in
# the live visual tree (a descendant of the Window), unlike StaticResource
# which would need to resolve at parse time against a dictionary we don't have.
_DISC_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <ComboBox ItemsSource="{DynamicResource DiscCodesResource}"
            SelectedItem="{Binding Disc, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
            IsEnabled="{Binding CanEditDisc}" FontFamily="Consolas" FontSize="11" BorderThickness="0.5"/>
</DataTemplate>
"""
_REF_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <ComboBox ItemsSource="{DynamicResource RefOptionsResource}" DisplayMemberPath="Label" SelectedValuePath="Id"
            SelectedValue="{Binding RefId, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
            IsEnabled="{Binding CanRef}" FontFamily="Consolas" FontSize="10.5" BorderThickness="0.5"/>
</DataTemplate>
"""
_STATUS_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Border CornerRadius="9" Padding="7,2" HorizontalAlignment="Left" Background="{Binding StatusBg}">
    <TextBlock Text="{Binding StatusDisplay}" FontFamily="Consolas" FontSize="10" FontWeight="Bold" Foreground="{Binding StatusFg}"/>
  </Border>
</DataTemplate>
"""
_HEB_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <ComboBox ItemsSource="{DynamicResource HebOptionsResource}"
            SelectedItem="{Binding Discipline, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
            FlowDirection="RightToLeft" FontSize="11" BorderThickness="0.5"/>
</DataTemplate>
"""
# Plain CheckBoxes bound directly, NOT DataGridCheckBoxColumn -- the latter
# needs the cell "current"/selected before a click registers, a well-known
# WPF quirk that made the include toggle feel unreliable. A CheckBox in a
# TemplateColumn is always live and toggles on a single click.
_SELECT_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <CheckBox IsChecked="{Binding Selected, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
            HorizontalAlignment="Center" VerticalAlignment="Center"
            ToolTip="Select for bulk include/exclude"/>
</DataTemplate>
"""
_INCLUDE_CELL_XAML = u"""
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <CheckBox IsChecked="{Binding Included, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
            IsEnabled="{Binding CanExclude}"
            HorizontalAlignment="Center" VerticalAlignment="Center"
            ToolTip="Included in the chart, report and issue export"/>
</DataTemplate>
"""


def _parse_fragment(xaml):
    ctx = SysXmlReader.Create(StringReader(xaml))
    return XamlReader.Load(ctx)


def _hbox(gap=8):
    p = WC.StackPanel()
    p.Orientation = WC.Orientation.Horizontal
    return p


def _tb(text, size=13, color="#374151", bold=False, wrap=False, margin=None):
    t = WC.TextBlock()
    t.Text = text
    t.FontSize = size
    t.Foreground = WM.BrushConverter().ConvertFromString(color)
    if bold:
        t.FontWeight = System.Windows.FontWeights.SemiBold
    if wrap:
        t.TextWrapping = System.Windows.TextWrapping.Wrap
    if margin:
        t.Margin = System.Windows.Thickness(*margin)
    return t


def _section_label(text):
    t = _tb(text.upper(), size=10.5, color="#9aa0ac")
    t.FontFamily = WM.FontFamily("Consolas")
    t.Margin = System.Windows.Thickness(2, 10, 0, 6)
    return t


def _card():
    b = WC.Border()
    b.Background = WM.Brushes.White
    b.BorderBrush = WM.BrushConverter().ConvertFromString("#e6e8f5")
    b.BorderThickness = System.Windows.Thickness(1)
    b.CornerRadius = System.Windows.CornerRadius(10)
    return b


def _checkbox(label, checked, handler):
    cb = WC.CheckBox()
    cb.Content = label
    cb.FontSize = 12.5
    cb.Margin = System.Windows.Thickness(0, 4, 0, 4)
    cb.IsChecked = bool(checked)
    if handler:
        cb.Checked += handler
        cb.Unchecked += handler
    return cb


class ProjectBasePointsDialog(object):
    """One instance per run. Call show(); when it returns, self.cancelled
    tells the caller whether an export actually completed."""

    def __init__(self, doc, host_info, rows):
        self.doc = doc
        self.host_info = host_info
        self.raw_rows = rows  # collected facts, never mutated after construction
        self.cancelled = True
        self._window = None
        self._syncing = False

        prefs = pbp_prefs.load_prefs(host_info)
        self.tol_mm = prefs.get("tolMm", 1)
        self.tol_deg = prefs.get("tolDeg", 0.02)
        self.opts = dict({"unloaded": True, "log": True, "remember": True, "notHub": False},
                          **(prefs.get("opts") or {}))
        self.disc_override = {}
        self.key_override = {}
        self.excluded = {}
        self.refs = {}
        self.filter = "all"
        self._restore_mapping(prefs.get("mapping"))
        # Anything the restore didn't cover (new links since the last save,
        # or no saved mapping at all) still gets auto-matched.
        for rid, target in auto_match_references(rows, self.disc_override, self.key_override).items():
            self.refs.setdefault(rid, target)

        default_folder = self._default_folder()
        self.exp = dict({
            "formats": {"html": True, "pdf": False, "xlsx": False},
            "onlyIssues": False, "includeUnresolved": True, "chart": True, "open": True,
            "folder": prefs.get("folder", default_folder),
        })
        self.acc = dict({
            "title": u"QA - Project Base Point", "titleShared": u"QA - Acquire Coordinates",
            "status": "Open", "category": "Design", "type": "BIM Quality",
            "assigneeType": "role", "dueDays": 7,
            "descElev": u"מפלס נקודת הבסיס אינו תואם את המודל האדריכלי.",
            "descAngle": u"הזווית לצפון אינה תואמת את המודל האדריכלי.",
            "descBoth": u"מפלס נקודת הבסיס והזווית לצפון אינם תואמים את המודל האדריכלי.",
            "descShared": u"יש לרכוש קורדינטות ממודל הסקר או מהמודל האדריכלי.",
            "cfMode": "none", "cfText": "", "cfList": [], "cfRole": {},
            "roles": {}, "heb": {}, "companies": {}, "rolesText": "", "companiesText": "",
            "extra": {}, "rowEdit": {},
        }, **self._restore_acc(prefs.get("acc")))

        self.stage = "setup"
        self._written_files = []
        self._last_issue_count = 0

    # ---- misc helpers ----
    def _default_folder(self):
        path = self.host_info.get("path") or ""
        if path and os.path.isdir(os.path.dirname(path)):
            return os.path.join(os.path.dirname(path), "PBP Reports")
        return os.path.join(os.path.expanduser("~"), "Documents", "PBP Reports")

    # ---- persistence: row ids are only stable within one collection run,
    # so anything saved across Revit sessions (mapping edits, ACC per-row
    # hand edits) is translated to/from a NAME-based key first. ----
    def _row_key(self, row):
        return row["link"] + (u"#" + row["inst"] if row.get("inst") else u"")

    def _row_by_key(self):
        return dict((self._row_key(r), r) for r in self.raw_rows)

    def _prune_stale_refs(self):
        """Drop any stored reference pointer that no longer points to a
        valid AR row (or HOST). resolve_rows() already ignores a stale
        pointer for status purposes, but without this it stays in
        self.refs and would silently reactivate if that row is ever
        re-marked AR again later -- call after anything that can change a
        row's AR-ness (a Disc edit, single or bulk, or the AR-config
        screen's toggle)."""
        by_id = dict((r["id"], r) for r in self.raw_rows)
        for rid, target_id in list(self.refs.items()):
            target = by_id.get(target_id)
            if target is None or (target["kind"] != "HOST" and not is_ar(target, self.disc_override)):
                self.refs.pop(rid, None)

    def _serialize_mapping(self):
        by_id = dict((r["id"], r) for r in self.raw_rows)
        disc = dict((self._row_key(by_id[rid]), v) for rid, v in self.disc_override.items() if rid in by_id)
        key = dict((self._row_key(by_id[rid]), v) for rid, v in self.key_override.items() if rid in by_id)
        excl = dict((self._row_key(by_id[rid]), True) for rid, v in self.excluded.items() if rid in by_id and v)
        refs = {}
        for rid, target_id in self.refs.items():
            if rid in by_id and target_id in by_id:
                refs[self._row_key(by_id[rid])] = self._row_key(by_id[target_id])
        return {"discOverride": disc, "keyOverride": key, "excluded": excl, "refs": refs}

    def _restore_mapping(self, saved):
        if not saved:
            return
        by_key = self._row_by_key()
        for name_key, v in (saved.get("discOverride") or {}).items():
            r = by_key.get(name_key)
            if r:
                self.disc_override[r["id"]] = v
        for name_key, v in (saved.get("keyOverride") or {}).items():
            r = by_key.get(name_key)
            if r:
                self.key_override[r["id"]] = v
        for name_key, v in (saved.get("excluded") or {}).items():
            r = by_key.get(name_key)
            if r and v:
                self.excluded[r["id"]] = True
        for src_key, dst_key in (saved.get("refs") or {}).items():
            src, dst = by_key.get(src_key), by_key.get(dst_key)
            if src and dst:
                self.refs[src["id"]] = dst["id"]

    def _serialize_acc(self):
        """A copy of self.acc with the two per-issue-row dicts (rowEdit,
        extra) translated from ephemeral row ids to stable name keys --
        everything else in acc is keyed by discipline CODE or is a plain
        setting, already stable as-is."""
        by_id = dict((r["id"], r) for r in self.raw_rows)
        out = dict(self.acc)
        for field in ("rowEdit", "extra"):
            src = self.acc.get(field) or {}
            out[field] = dict((self._row_key(by_id[rid]), v) for rid, v in src.items() if rid in by_id)
        return out

    def _restore_acc(self, saved_acc):
        if not saved_acc:
            return {}
        by_key = self._row_by_key()
        out = dict(saved_acc)
        for field in ("rowEdit", "extra"):
            src = saved_acc.get(field) or {}
            translated = {}
            for name_key, v in src.items():
                r = by_key.get(name_key)
                if r:
                    translated[r["id"]] = v
            out[field] = translated
        return out

    def _save_all_prefs(self):
        """Explicit "Save" button handler AND the safety-net call on close --
        persists everything across every stage (tolerances, options,
        destination folder, the Results mapping, and the full ACC wizard
        state), regardless of whether an export has actually run. Ignores
        the "Remember mapping" toggle -- that toggle controls whether a
        FUTURE run auto-loads this, not whether Save itself works."""
        pbp_prefs.save_prefs(self.host_info, {
            "tolMm": self.tol_mm, "tolDeg": self.tol_deg, "opts": self.opts,
            "folder": self.exp["folder"], "acc": self._serialize_acc(),
            "mapping": self._serialize_mapping(),
        })

    def _resolved(self):
        rows = resolve_rows(self.raw_rows, self.disc_override, self.refs, self.excluded,
                             self.tol_mm, self.tol_deg, key_override=self.key_override)
        if not self.opts.get("unloaded", True):
            # "List unloaded links" off -- drop them from the results table,
            # chart, and every report/export; Setup's own stat tiles still
            # count them from self.raw_rows directly so nothing goes missing
            # from view entirely, just from the working list.
            rows = [r for r in rows if r["kind"] == "HOST" or r["placed"]]
        return rows

    def _all_disc_codes(self):
        # "-" (the disabled HOST row's Disc cell) must be in the list too, or
        # its ComboBox.SelectedItem binding finds no match and renders blank.
        codes = ["-"] + list(DISC_CODES)
        for r in self.raw_rows:
            if r["disc"] not in codes:
                codes.append(r["disc"])
        return codes

    # ---- build/show ----
    def _build(self):
        w = _parse_fragment(XAML)
        self._window = w
        w.Resources["DiscCodesResource"] = NetList[System.String](self._all_disc_codes())
        w.Resources["HebOptionsResource"] = NetList[System.String](HEB_OPTIONS)

        self._ref_table = SD.DataTable("RefOptions")
        self._ref_table.Columns.Add("Id", int)
        self._ref_table.Columns.Add("Label", str)
        w.Resources["RefOptionsResource"] = self._ref_table.DefaultView

        w.FindName(u"CloseBtn").Click += self._on_cancel
        # Window.Closing fires for EVERY close path -- the custom in-content
        # "X"/Cancel/Done buttons (which just call window.Close()), the
        # NATIVE OS title-bar close button, and Alt+F4. Hooking it here
        # (rather than only the custom button's own Click handler) is what
        # makes "save on close" actually cover all of those.
        w.Closing += self._on_window_closing

        # Font/UI zoom: a single ScaleTransform over the whole window content
        # (simplest way to scale every hand-set FontSize consistently without
        # touching each one) -- "A+"/"A-" buttons and Ctrl+scroll anywhere.
        self._zoom = 1.0
        self._zoom_transform = WM.ScaleTransform(1.0, 1.0)
        w.FindName(u"RootGrid").LayoutTransform = self._zoom_transform
        w.FindName(u"ZoomInBtn").Click += self._on_zoom_in
        w.FindName(u"ZoomOutBtn").Click += self._on_zoom_out
        w.PreviewMouseWheel += self._on_mouse_wheel

        self._setup_panel = w.FindName(u"SetupPanel")
        self._results_host = w.FindName(u"ResultsHost")
        self._ar_config_panel = w.FindName(u"ArConfigPanel")
        self._export_panel = w.FindName(u"ExportPanel")
        self._issues_panel = w.FindName(u"IssuesPanel")
        self._done_panel = w.FindName(u"DonePanel")
        self._banner = w.FindName(u"BannerText")
        self._footer_status = w.FindName(u"FooterStatusTB")
        self._footer_buttons = w.FindName(u"FooterButtons")

        self._build_results_host()
        self._go_to_stage("setup")
        return w

    def show(self):
        w = self._build()
        frame = DispatcherFrame()

        def on_closed(s, e):
            frame.Continue = False
        w.Closed += on_closed
        w.Show()
        Dispatcher.PushFrame(frame)
        return self.cancelled

    def _on_cancel(self, sender, args):
        # "Cancel"/"Done"/the header X all just close the dialog -- saving
        # itself happens centrally in _on_window_closing, which also covers
        # the native OS close button and Alt+F4 (neither of which goes
        # through this handler at all).
        self._window.Close()

    def _on_window_closing(self, sender, args):
        try:
            if self.opts.get("remember"):
                self._save_all_prefs()
        except Exception:
            logger.warning("Could not save preferences on close.")

    # ---- zoom ----
    _ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP = 0.8, 1.5, 0.1

    def _apply_zoom(self):
        self._zoom_transform.ScaleX = self._zoom
        self._zoom_transform.ScaleY = self._zoom

    def _on_zoom_in(self, sender, args):
        self._zoom = round(min(self._ZOOM_MAX, self._zoom + self._ZOOM_STEP), 2)
        self._apply_zoom()

    def _on_zoom_out(self, sender, args):
        self._zoom = round(max(self._ZOOM_MIN, self._zoom - self._ZOOM_STEP), 2)
        self._apply_zoom()

    def _on_mouse_wheel(self, sender, args):
        ctrl_down = bool(System.Windows.Input.Keyboard.Modifiers & System.Windows.Input.ModifierKeys.Control)
        if not ctrl_down:
            return
        if args.Delta > 0:
            self._on_zoom_in(sender, args)
        else:
            self._on_zoom_out(sender, args)
        args.Handled = True

    def _on_save_click(self, sender, args):
        try:
            self._save_all_prefs()
        except Exception:
            logger.error(traceback.format_exc())
            System.Windows.MessageBox.Show(
                u"Could not save:\n\n" + traceback.format_exc(), u"Project Base Points",
                System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error)
            return
        btn = sender
        original = btn.Content
        btn.Content = u"Saved ✓"
        btn.IsEnabled = False
        timer = DispatcherTimer()
        timer.Interval = System.TimeSpan.FromSeconds(1.4)
        def revert(s, e):
            btn.Content = original
            btn.IsEnabled = True
            timer.Stop()
        timer.Tick += revert
        timer.Start()

    # ---- stage switching ----
    def _go_to_stage(self, stage):
        # Every stage transition funnels through here (Run audit, Settings,
        # Export report, Next: issue fields, Back, Done, ...) -- wrapped so
        # an exception mid-transition surfaces as a visible error instead of
        # leaving the dialog looking "stuck" with no feedback at all.
        try:
            self._go_to_stage_impl(stage)
        except Exception:
            logger.error(traceback.format_exc())
            System.Windows.MessageBox.Show(
                u"Could not open the \"{}\" screen:\n\n".format(stage) + traceback.format_exc(),
                u"Project Base Points", System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error)

    def _go_to_stage_impl(self, stage):
        prev_stage = self.stage
        self.stage = stage
        w = self._window
        w.FindName(u"SetupScroll").Visibility = self._vis(stage == "setup")
        self._results_host.Visibility = self._vis(stage == "results")
        w.FindName(u"ArConfigScroll").Visibility = self._vis(stage == "ar_config")
        w.FindName(u"ExportScroll").Visibility = self._vis(stage == "export")
        w.FindName(u"IssuesScroll").Visibility = self._vis(stage == "issues")
        w.FindName(u"DoneScroll").Visibility = self._vis(stage == "done")

        banners = {
            "setup": u"Reads the Project Base Point of the host model and every directly-placed link, then checks "
                     u"each discipline model against its matching Architecture reference. Nested links are excluded.",
            "ar_config": u"Tick every link that is actually an Architecture reference model, regardless of what its "
                         u"file name parsed to. Unticking a link that WAS auto-detected as AR clears it back to "
                         u"its own discipline instead.",
            "results": None,
            "export": u"The report replaces the old Dynamo HTML output — pick any combination of formats. "
                      u"The Excel sheet is an ACC issue import.",
            "issues": u"Every value below is written into the ACC import sheet. Anything your project names "
                      u"differently — statuses, types, roles, custom fields — can be corrected here first.",
            "done": None,
        }
        text = banners.get(stage)
        self._banner.Text = text or ""
        self._banner.Visibility = self._vis(bool(text))
        (w.FindName(u"BannerBorder")).Visibility = self._vis(bool(text))

        if stage == "setup":
            if prev_stage == "ar_config":
                # Leaving the AR-config screen: the AR set may have just
                # changed, so re-derive every reference fresh from it --
                # same full recompute "Reset mapping" already does. If the
                # user had already hand-picked some references in Results
                # before coming back here to fix AR detection, those picks
                # are reset too (there's no separate tracking of "auto" vs
                # "hand-picked" refs to preserve selectively).
                self.refs = auto_match_references(self.raw_rows, self.disc_override, self.key_override)
            self._build_setup_panel()
        elif stage == "ar_config":
            self._refresh_ar_config_panel()
        elif stage == "results":
            self._refresh_results_grid()
        elif stage == "export":
            self._refresh_export_panel()
        elif stage == "issues":
            self._refresh_issues_panel()
        elif stage == "done":
            self._refresh_done_panel()

        self._refresh_footer()

    def _vis(self, on):
        return System.Windows.Visibility.Visible if on else System.Windows.Visibility.Collapsed

    def _refresh_footer(self):
        buttons = self._footer_buttons
        buttons.Children.Clear()
        resolved = self._resolved()
        not_ok = sum(1 for r in resolved if r["included"] and r["status"] == "Not OK")
        unshared = sum(1 for r in resolved if r["included"] and r["status"] == "Not Shared")

        def add_btn(text, style_key, handler, enabled=True):
            b = WC.Button()
            b.Content = text
            b.Style = self._window.FindResource(style_key)
            b.Margin = System.Windows.Thickness(8, 0, 0, 0)
            b.IsEnabled = enabled
            b.Click += handler
            buttons.Children.Add(b)
            return b

        # Present on every stage -- saves tolerances/options/destination, the
        # full Results mapping (disc/building overrides, references,
        # include/exclude) and the whole ACC wizard state in one action,
        # regardless of which screen you're currently on or whether an
        # export has run yet.
        add_btn(u"Save", "GhostBtn", self._on_save_click).ToolTip = (
            u"Save every setting on every screen (tolerances, results mapping, export options, "
            u"ACC issue fields) so re-opening this tool on this model restores it. Saved to:\n" +
            pbp_prefs.prefs_file_path())

        if self.stage == "setup":
            self._footer_status.Text = u"{} mm / {}°{}".format(
                self.tol_mm, self.tol_deg, u" · external hub" if self.opts.get("notHub") else "")
            add_btn(u"Cancel", "GhostBtn", self._on_cancel)
            add_btn(u"Run audit", "PrimaryBtn", self._on_run_audit)
        elif self.stage == "ar_config":
            ar_count = sum(1 for r in self.raw_rows if r["kind"] != "HOST" and is_ar(r, self.disc_override))
            self._footer_status.Text = u"{} link{} marked as AR reference".format(
                ar_count, u"" if ar_count == 1 else u"s")
            add_btn(u"Done", "PrimaryBtn", lambda s, e: self._go_to_stage("setup"))
        elif self.stage == "results":
            self._footer_status.Text = u"{} link{} not coordinated · {} link{} not in shared coordinates".format(
                not_ok, "" if not_ok == 1 else "s", unshared, "" if unshared == 1 else "s")
            add_btn(u"Settings", "GhostBtn", lambda s, e: self._go_to_stage("setup"))
            add_btn(u"Export report…", "PrimaryBtn", lambda s, e: self._go_to_stage("export"))
        elif self.stage == "export":
            picks = [k for k in ("html", "pdf", "xlsx") if self.exp["formats"].get(k)]
            self._footer_status.Text = (u" + ".join(picks) if picks else u"no format selected")
            add_btn(u"Back to results", "GhostBtn", lambda s, e: self._go_to_stage("results"))
            if self.exp["formats"].get("xlsx"):
                add_btn(u"Next: issue fields", "PrimaryBtn", lambda s, e: self._go_to_stage("issues"),
                        enabled=len(picks) > 0)
            else:
                add_btn(u"Export", "PrimaryBtn", self._on_export_click, enabled=len(picks) > 0)
        elif self.stage == "issues":
            issue_rows = self._issue_rows_current()
            gaps = sum(1 for x in issue_rows if not x.get("Assigned To"))
            self._footer_status.Text = u"{} issue{} {}".format(
                len(issue_rows), "" if len(issue_rows) == 1 else "s",
                (u"· {} missing assignee".format(gaps) if gaps else u"· all assignees mapped"))
            add_btn(u"Back", "GhostBtn", lambda s, e: self._go_to_stage("export"))
            add_btn(u"Export sheet", "PrimaryBtn", self._on_export_click)
        else:  # done
            self._footer_status.Text = ""
            add_btn(u"Open destination folder", "GhostBtn", self._on_open_folder)
            if self.exp["formats"].get("xlsx"):
                add_btn(u"Issue fields", "GhostBtn", lambda s, e: self._go_to_stage("issues"))
            else:
                add_btn(u"Back to results", "GhostBtn", lambda s, e: self._go_to_stage("results"))
            add_btn(u"Done", "PrimaryBtn", self._on_cancel)

    # =========================================================== SETUP
    def _build_setup_panel(self):
        p = self._setup_panel
        p.Children.Clear()
        h = self.host_info

        p.Children.Add(_section_label(u"Host model"))
        host_card = _card()
        host_card.Padding = System.Windows.Thickness(14, 10, 14, 10)
        row = WC.Grid()
        row.ColumnDefinitions.Add(WC.ColumnDefinition())
        row.ColumnDefinitions.Add(WC.ColumnDefinition())
        row.ColumnDefinitions[1].Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        title_stack = WC.StackPanel()
        title_tb = _tb(h["title"], size=13, color=NAVY, bold=True)
        title_tb.FontFamily = WM.FontFamily("Consolas")
        title_stack.Children.Add(title_tb)
        sub = u"{} · units: {}{}".format(h.get("path", ""), h.get("units", "?"),
                                               u" · workshared" if h.get("workshared") else "")
        title_stack.Children.Add(_tb(sub, size=11, color="#9aa0ac"))
        WC.Grid.SetColumn(title_stack, 1)
        row.Children.Add(title_stack)
        host_card.Child = row
        p.Children.Add(host_card)

        # Architecture reference models
        p.Children.Add(_section_label(u"Architecture reference models · the baseline every discipline is checked against"))
        ar_card = _card()
        ar_panel = WC.StackPanel()
        resolved = self._resolved()
        ars = [r for r in resolved if r["kind"] != "HOST" and r["status"] == "Reference" and r["placed"]]
        if not ars:
            warn = _tb(u"No Architecture model found yet — every link will report Missing Ref until "
                       u"disciplines are re-mapped in the results table.", size=12, color=ORANGE, wrap=True)
            warn.Margin = System.Windows.Thickness(12)
            ar_panel.Children.Add(warn)
        else:
            hdr = WC.Grid()
            for w_ in (2, 1, 1, 1, 1):
                cd = WC.ColumnDefinition()
                cd.Width = System.Windows.GridLength(w_, System.Windows.GridUnitType.Star)
                hdr.ColumnDefinitions.Add(cd)
            for i, htext in enumerate(["Model", "Building", "Shared site", "Elev (m)", "Angle (deg)"]):
                t = _tb(htext.upper(), size=9.5, color="#8b93a7", bold=True)
                t.Margin = System.Windows.Thickness(10, 7, 10, 7)
                WC.Grid.SetColumn(t, i)
                hdr.Children.Add(t)
            hdr.Background = WM.BrushConverter().ConvertFromString("#f4f6fd")
            ar_panel.Children.Add(hdr)
            for r in ars:
                gr = WC.Grid()
                for w_ in (2, 1, 1, 1, 1):
                    cd = WC.ColumnDefinition()
                    cd.Width = System.Windows.GridLength(w_, System.Windows.GridUnitType.Star)
                    gr.ColumnDefinitions.Add(cd)
                vals = [r["link"], r["key"],
                        (u"Not Shared" if r["shared"] == "No" else r["site"]),
                        _fmt(r["el"]), (u"—" if r["ang"] is None else "{:.4f}".format(r["ang"]))]
                for i, v in enumerate(vals):
                    t = _tb(v, size=11.5, color=(RED if i == 2 and r["shared"] == "No" else "#374151"))
                    t.Margin = System.Windows.Thickness(10, 6, 10, 6)
                    WC.Grid.SetColumn(t, i)
                    gr.Children.Add(t)
                ar_panel.Children.Add(gr)
        ar_card.Child = ar_panel
        p.Children.Add(ar_card)

        ar_config_btn = WC.Button()
        ar_config_btn.Content = u"Configure AR references…"
        ar_config_btn.Style = self._window.FindResource("GhostBtn")
        ar_config_btn.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
        ar_config_btn.Margin = System.Windows.Thickness(0, 0, 0, 14)
        ar_config_btn.ToolTip = u"Manually mark which links ARE Architecture reference models, for when the file name didn't parse correctly"
        ar_config_btn.Click += lambda s, e: self._go_to_stage("ar_config")
        p.Children.Add(ar_config_btn)

        # stat tiles
        links = [r for r in self.raw_rows if r["kind"] != "HOST"]
        unloaded = sum(1 for r in links if not r["placed"])
        not_shared = sum(1 for r in links if r["placed"] and r["shared"] == "No")
        stats = WC.Grid()
        stats.Margin = System.Windows.Thickness(0, 4, 0, 0)
        tiles = [(len(links), "Link instances"), (self.host_info.get("nested", 0), "Nested · skipped"),
                 (len(ars), "AR references"), (not_shared, "Not shared"), (unloaded, "Unloaded")]
        for i in range(5):
            stats.ColumnDefinitions.Add(WC.ColumnDefinition())
        for i, (val, label) in enumerate(tiles):
            tile = _card()
            tile.Margin = System.Windows.Thickness(4 if i else 0, 0, 4 if i < 4 else 0, 0)
            tile.Padding = System.Windows.Thickness(10)
            stk = WC.StackPanel()
            vtb = _tb(str(val), size=21, color=NAVY, bold=True)
            vtb.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
            stk.Children.Add(vtb)
            ltb = _tb(label, size=10, color="#6b7280")
            ltb.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
            ltb.TextAlignment = System.Windows.TextAlignment.Center
            stk.Children.Add(ltb)
            tile.Child = stk
            WC.Grid.SetColumn(tile, i)
            stats.Children.Add(tile)
        p.Children.Add(stats)

        # Tolerances & options (always-open section, no disclosure — keeps wiring simple)
        p.Children.Add(_section_label(u"Tolerances &amp; options"))
        tol_card = _card()
        tol_stack = WC.StackPanel()
        tol_stack.Margin = System.Windows.Thickness(14, 12, 14, 12)

        tol_row = WC.Grid()
        for _ in range(3):
            tol_row.ColumnDefinitions.Add(WC.ColumnDefinition())

        def labeled_box(label, value, col, suffix, on_change):
            stk = WC.StackPanel()
            stk.Margin = System.Windows.Thickness(0 if col == 0 else 8, 0, 0, 10)
            stk.Children.Add(_tb(label.upper(), size=9.5, color="#9aa0ac"))
            box = WC.TextBox()
            box.Text = str(value)
            box.FontFamily = WM.FontFamily("Consolas")
            box.Padding = System.Windows.Thickness(8, 5, 8, 5)
            box.TextChanged += on_change
            stk.Children.Add(box)
            WC.Grid.SetColumn(stk, col)
            tol_row.Children.Add(stk)
            return box

        def on_tolmm(s, e):
            try:
                self.tol_mm = float(s.Text)
            except Exception:
                pass
        def on_toldeg(s, e):
            try:
                self.tol_deg = float(s.Text)
            except Exception:
                pass
        labeled_box(u"Position tolerance (mm)", self.tol_mm, 0, "mm", on_tolmm)
        labeled_box(u"Angle tolerance (deg)", self.tol_deg, 1, "deg", on_toldeg)
        ar_codes_stk = WC.StackPanel()
        ar_codes_stk.Margin = System.Windows.Thickness(8, 0, 0, 10)
        ar_codes_stk.Children.Add(_tb(u"ARCHITECTURE CODES", size=9.5, color="#9aa0ac"))
        ar_codes_stk.Children.Add(_tb(", ".join(AR_CODES), size=11.5, color=NAVY))
        WC.Grid.SetColumn(ar_codes_stk, 2)
        tol_row.Children.Add(ar_codes_stk)
        tol_stack.Children.Add(tol_row)

        def on_toggle(key):
            def handler(s, e):
                self.opts[key] = bool(s.IsChecked)
            return handler

        toggle_defs = [
            ("unloaded", u"List unloaded links", u"Shown greyed, without coordinates."),
            ("log", u"Write a run log alongside the report", u"Records every link processed and its computed values."),
            ("remember", u"Remember mapping &amp; destination for this host model",
             u"Tolerances, options, folder and ACC wizard state restore next run (this Windows user only)."),
            ("notHub", u"Not in EasyBIM Hub",
             u"This project sits on another hub — the issue export asks for its own role names, no built-in fallback."),
        ]
        for key, label, hint in toggle_defs:
            row_stk = WC.StackPanel()
            row_stk.Margin = System.Windows.Thickness(0, 2, 0, 6)
            cb = _checkbox(label, self.opts.get(key, False), on_toggle(key))
            row_stk.Children.Add(cb)
            hint_tb = _tb(hint, size=10.5, color="#9aa0ac", wrap=True)
            hint_tb.Margin = System.Windows.Thickness(22, 0, 0, 0)
            row_stk.Children.Add(hint_tb)
            tol_stack.Children.Add(row_stk)

        tol_card.Child = tol_stack
        p.Children.Add(tol_card)

        info = _tb(u"Each discipline model is matched to the Architecture model sharing its building key "
                   u"(ST-BLD_A → AR-BLD_A). Matching is best-effort — discipline, reference and "
                   u"inclusion are all editable in the results table.", size=11.5, color="#6b7280", wrap=True)
        info.Margin = System.Windows.Thickness(2, 10, 2, 4)
        p.Children.Add(info)

    def _on_run_audit(self, sender, args):
        self._go_to_stage("results")

    # =========================================================== AR CONFIG
    def _refresh_ar_config_panel(self):
        # Built once per stage-entry: the filter TextBox itself is never
        # recreated after this (only _rebuild_ar_config_list's content is,
        # on every keystroke) -- that's what makes live filtering possible
        # without kicking focus out of the box after each character typed.
        p = self._ar_config_panel
        p.Children.Clear()

        intro = _tb(u"Tick every link that IS actually an Architecture reference model -- useful when a file "
                   u"name doesn't follow the usual PROJECT-DISCIPLINE-FIRM-BUILDING-VERSION convention and the "
                   u"discipline parsed wrong. This is the single most important setting: every other link is "
                   u"checked against whichever links are marked here.", size=12, color="#374151", wrap=True)
        intro.Margin = System.Windows.Thickness(2, 0, 2, 10)
        p.Children.Add(intro)

        p.Children.Add(_section_label(u"Filter by link name"))
        filter_box = WC.TextBox()
        filter_box.Padding = System.Windows.Thickness(8, 5, 8, 5)
        filter_box.Margin = System.Windows.Thickness(0, 0, 0, 10)
        filter_box.Text = getattr(self, "_ar_config_filter", "")
        def on_filter_change(s, e):
            self._ar_config_filter = s.Text
            self._rebuild_ar_config_list()
        filter_box.TextChanged += on_filter_change
        self._ar_config_filter_box = filter_box
        p.Children.Add(filter_box)

        self._ar_config_list_card = _card()
        p.Children.Add(self._ar_config_list_card)
        self._rebuild_ar_config_list()

    def _rebuild_ar_config_list(self):
        links = [r for r in self.raw_rows if r["kind"] != "HOST"]
        filter_text = getattr(self, "_ar_config_filter", "")
        list_stk = WC.StackPanel()

        head = _hbox()
        head.Margin = System.Windows.Thickness(10, 8, 0, 4)
        def head_cell(text, width):
            t = _tb(text.upper(), size=9, color="#9aa0ac", bold=True)
            t.Width = width
            head.Children.Add(t)
        head_cell(u"AR?", 40)
        head_cell(u"Link", 300)
        head_cell(u"Disc (current)", 100)
        head_cell(u"Bldg", 90)
        head_cell(u"Shared Site", 130)
        list_stk.Children.Add(head)

        shown = [r for r in links if filter_text.lower() in r["link"].lower()] if filter_text else links
        shown = sorted(shown, key=lambda x: x["link"])
        if not shown:
            empty = _tb(u"No links match this filter." if filter_text else u"No links found.",
                        size=11.5, color="#9aa0ac")
            empty.Margin = System.Windows.Thickness(10, 8, 10, 8)
            list_stk.Children.Add(empty)
        for r in shown:
            row_ = _hbox()
            row_.Margin = System.Windows.Thickness(10, 3, 0, 3)
            cb = WC.CheckBox()
            cb.Width = 40
            cb.IsChecked = is_ar(r, self.disc_override)
            def on_toggle(s, e, rid=r["id"], raw_disc=r["disc"]):
                if s.IsChecked:
                    self.disc_override[rid] = "AR"
                elif raw_disc in AR_CODES:
                    # Raw parse itself said AR -- must override to
                    # something else or unchecking would have no effect.
                    self.disc_override[rid] = "other"
                else:
                    self.disc_override.pop(rid, None)
                self._prune_stale_refs()
                self._rebuild_ar_config_list()
            cb.Checked += on_toggle
            cb.Unchecked += on_toggle
            row_.Children.Add(cb)
            link_tb = _tb(r["link"] + (u" " + r["inst"] if r["inst"] else u""), size=11.5, color=NAVY, bold=True)
            link_tb.FontFamily = WM.FontFamily("Consolas")
            link_tb.Width = 300
            row_.Children.Add(link_tb)
            disc_tb = _tb(disc_of(r, self.disc_override), size=11, color="#6b7280")
            disc_tb.FontFamily = WM.FontFamily("Consolas")
            disc_tb.Width = 100
            row_.Children.Add(disc_tb)
            bldg_tb = _tb(r["key"], size=11, color="#6b7280")
            bldg_tb.Width = 90
            row_.Children.Add(bldg_tb)
            if r["shared"] == "No":
                site_text, site_color = u"Not Shared", RED
            elif r["shared"] == "?":
                site_text, site_color = u"—", MUTE
            else:
                site_text, site_color = (r["site"] or u"—"), "#6b7280"
            site_tb = _tb(site_text, size=11, color=site_color)
            site_tb.Width = 130
            row_.Children.Add(site_tb)
            list_stk.Children.Add(row_)

        self._ar_config_list_card.Child = list_stk
        self._refresh_footer()

    # =========================================================== RESULTS
    RESULTS_COLS = [
        (u"Sel", 48, "Select"), (u"Incl.", 46, "IncludeToggle"),
        (u"Link", 170, "LinkDisp"), (u"Disc", 62, "Disc"), (u"Bldg", 62, "Bldg"),
        (u"Shared Site", 110, "SharedSite"), (u"Workset", 90, "Workset"),
        (u"N/S (m)", 78, "NS"), (u"E/W (m)", 78, "EW"), (u"Elev (m)", 70, "Elev"), (u"Angle", 66, "Angle"),
        (u"Reference (AR)", 150, "RefDisp"), (u"Check", 118, "Status"),
    ]

    def _build_results_host(self):
        host = self._results_host
        self._selected_ids = set()
        host.RowDefinitions.Add(WC.RowDefinition())
        host.RowDefinitions[0].Height = System.Windows.GridLength.Auto
        host.RowDefinitions.Add(WC.RowDefinition())
        host.RowDefinitions[1].Height = System.Windows.GridLength.Auto
        host.RowDefinitions.Add(WC.RowDefinition())
        host.RowDefinitions[2].Height = System.Windows.GridLength.Auto
        host.RowDefinitions.Add(WC.RowDefinition())
        host.RowDefinitions[3].Height = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)

        self._chart_panel = WC.StackPanel()
        WC.Grid.SetRow(self._chart_panel, 0)
        host.Children.Add(self._chart_panel)

        chip_row = _hbox()
        chip_row.Margin = System.Windows.Thickness(0, 8, 0, 8)
        WC.Grid.SetRow(chip_row, 1)
        self._chip_row = chip_row
        host.Children.Add(chip_row)

        # Bulk-edit bar: appears once >=1 row is ticked in the "Sel" column,
        # separate from each row's own "Incl." (report inclusion) toggle.
        # Lets several selected rows' Disc / Reference (AR) / Included all be
        # set to one value in a single action.
        self._bulk_field = "disc"
        bulk_card = _card()
        bulk_card.Margin = System.Windows.Thickness(0, 0, 0, 8)
        bulk_card.Padding = System.Windows.Thickness(10, 8, 10, 8)
        bulk_card.Visibility = System.Windows.Visibility.Collapsed
        bulk_card.Background = WM.BrushConverter().ConvertFromString("#F0FAFC")
        bulk_card.BorderBrush = WM.BrushConverter().ConvertFromString(CYAN)
        bulk_bar = WC.StackPanel()
        bulk_card.Child = bulk_bar
        WC.Grid.SetRow(bulk_card, 2)
        self._bulk_bar = bulk_bar
        self._bulk_card = bulk_card
        host.Children.Add(bulk_card)

        # DataTable backing the grid
        dt = SD.DataTable("Results")
        for name, clr_type in [("Id", int), ("Selected", bool), ("Included", bool), ("CanExclude", bool),
                                ("LinkDisp", str), ("Disc", str), ("CanEditDisc", bool),
                                ("Bldg", str), ("SharedSite", str), ("Workset", str),
                                ("NS", str), ("EW", str), ("Elev", str), ("Angle", str),
                                ("RefId", int), ("CanRef", bool),
                                ("Status", str), ("StatusDisplay", str), ("StatusFg", str), ("StatusBg", str),
                                ("IsHost", bool)]:
            dt.Columns.Add(name, clr_type)
        self._results_table = dt
        dt.RowChanged += self._on_results_row_changed

        grid = WC.DataGrid()
        grid.AutoGenerateColumns = False
        grid.CanUserAddRows = False
        grid.CanUserDeleteRows = False
        grid.RowHeaderWidth = 0
        grid.FontSize = 11
        grid.GridLinesVisibility = WC.DataGridGridLinesVisibility.Horizontal
        grid.HorizontalGridLinesBrush = WM.BrushConverter().ConvertFromString("#eef0f7")
        grid.AlternatingRowBackground = WM.BrushConverter().ConvertFromString("#fafbff")

        row_style = System.Windows.Style(clr.GetClrType(WC.DataGridRow))
        host_trigger = System.Windows.DataTrigger()
        host_trigger.Binding = System.Windows.Data.Binding("IsHost")
        host_trigger.Value = True
        host_trigger.Setters.Add(System.Windows.Setter(WC.DataGridRow.BackgroundProperty,
                                                         WM.BrushConverter().ConvertFromString("#EEF0FA")))
        row_style.Triggers.Add(host_trigger)
        excl_trigger = System.Windows.DataTrigger()
        excl_trigger.Binding = System.Windows.Data.Binding("Included")
        excl_trigger.Value = False
        excl_trigger.Setters.Add(System.Windows.Setter(WC.DataGridRow.OpacityProperty, 0.42))
        row_style.Triggers.Add(excl_trigger)
        grid.RowStyle = row_style

        for header, width, binding in self.RESULTS_COLS:
            if binding == "Disc":
                col = WC.DataGridTemplateColumn()
                col.CellTemplate = _parse_fragment(_DISC_CELL_XAML)
                col.SortMemberPath = "Disc"
            elif binding == "RefDisp":
                col = WC.DataGridTemplateColumn()
                col.CellTemplate = _parse_fragment(_REF_CELL_XAML)
                col.SortMemberPath = "RefId"
            elif binding == "Status":
                col = WC.DataGridTemplateColumn()
                col.CellTemplate = _parse_fragment(_STATUS_CELL_XAML)
                col.SortMemberPath = "Status"
            elif binding == "Select":
                col = WC.DataGridTemplateColumn()
                col.CellTemplate = _parse_fragment(_SELECT_CELL_XAML)
                select_all_cb = WC.CheckBox()
                select_all_cb.ToolTip = u"Select/deselect all rows (ignores the current filter)"
                select_all_cb.Click += self._on_select_all_click
                header_stack = WC.StackPanel()
                header_stack.Orientation = WC.Orientation.Horizontal
                header_stack.ToolTip = u"Tick rows here to bulk-edit or bulk-include/exclude them together"
                sel_label = _tb(u"Sel", size=9, color="#8b93a7", bold=True)
                sel_label.VerticalAlignment = System.Windows.VerticalAlignment.Center
                sel_label.Margin = System.Windows.Thickness(0, 0, 3, 0)
                header_stack.Children.Add(sel_label)
                header_stack.Children.Add(select_all_cb)
                col.Header = header_stack
            elif binding == "IncludeToggle":
                col = WC.DataGridTemplateColumn()
                col.CellTemplate = _parse_fragment(_INCLUDE_CELL_XAML)
            elif binding == "Bldg":
                # Editable free text -- fixing the building key here drives
                # this row's own Reference (AR) to re-match automatically
                # (see _on_results_row_changed). Disabled for the HOST row,
                # same "is a link row" condition as the Disc column.
                col = WC.DataGridTextColumn()
                col.Binding = System.Windows.Data.Binding("Bldg")
                col.SortMemberPath = "Bldg"
                eestyle = System.Windows.Style(clr.GetClrType(WC.TextBox))
                eestyle.Setters.Add(System.Windows.Setter(WC.TextBox.IsEnabledProperty,
                                                           System.Windows.Data.Binding("CanEditDisc")))
                col.EditingElementStyle = eestyle
            else:
                col = WC.DataGridTextColumn()
                col.Binding = System.Windows.Data.Binding(binding)
                col.IsReadOnly = True
                col.SortMemberPath = binding
            if binding != "Select":
                col.Header = header
            col.Width = WC.DataGridLength(width)
            grid.Columns.Add(col)

        self._results_grid = grid
        WC.Grid.SetRow(grid, 3)
        host.Children.Add(grid)

    def _rebuild_ref_options(self, resolved):
        self._ref_table.Rows.Clear()
        self._ref_table.Rows.Add(-1, u"— none —")
        # Two AR models can share an identical file name while sitting on
        # different shared sites -- ar_option_labels appends the site in
        # brackets for those specifically, so they're distinguishable here.
        labels = ar_option_labels(self.raw_rows, self.disc_override)
        for r in resolved:
            if r["placed"] and (r["kind"] == "HOST" or r["status"] == "Reference"):
                self._ref_table.Rows.Add(r["id"], labels.get(r["id"], r["link"]))

    def _refresh_results_grid(self):
        resolved = self._resolved()
        self._rebuild_ref_options(resolved)
        live_ids = set(r["id"] for r in resolved)
        self._selected_ids &= live_ids
        self._syncing = True
        try:
            dt = self._results_table
            dt.Rows.Clear()
            for r in resolved:
                is_link = r["kind"] != "HOST"
                can_ref = is_link and r["placed"] and r["status"] not in ("Reference",)
                ref_id = self.refs.get(r["id"], -1) if can_ref else -1
                dt.Rows.Add(
                    r["id"], r["id"] in self._selected_ids, r["included"], is_link,
                    r["link"] + (" " + r["inst"] if r["inst"] else ""),
                    r["disc"], is_link,
                    r["key"],
                    ("-" if r["kind"] == "HOST" else (u"Not Shared" if r["shared"] == "No" else
                     (u"—" if r["shared"] == "?" else r["site"]))),
                    r["workset"], _fmt(r["ns"]), _fmt(r["ew"]), _fmt(r["el"]),
                    (u"—" if r["ang"] is None else "{:.4f}".format(r["ang"])),
                    ref_id, can_ref,
                    r["status"], _status_display(r["status"], r.get("reason")),
                    TONE.get(r["status"], MUTE), BG.get(r["status"], "#f1f2f7"),
                    r["kind"] == "HOST",
                )
        finally:
            self._syncing = False
        self._results_grid.ItemsSource = self._results_table.DefaultView
        self._apply_results_filter()
        self._rebuild_chart(resolved)
        self._rebuild_chips(resolved)
        self._refresh_bulk_bar()

    def _on_results_row_changed(self, sender, e):
        if self._syncing:
            return
        row = e.Row
        row_id = int(row["Id"])
        raw = next((r for r in self.raw_rows if r["id"] == row_id), None)
        if raw is None:
            return

        try:
            if bool(row["Selected"]):
                self._selected_ids.add(row_id)
            else:
                self._selected_ids.discard(row_id)
        except Exception:
            pass

        # DataTable.RowChanged doesn't say WHICH column changed, so diff each
        # editable cell against what's currently tracked -- whichever one
        # actually differs is the edit that just happened (WPF commits one
        # cell at a time, so only one of these differs on any given call).
        # "Selected" alone is NOT substantive -- it doesn't affect status,
        # matching, or any export, so it skips the (heavier) full rebuild.
        substantive = False
        bldg_changed = False
        try:
            new_key = str(row["Bldg"])
            if new_key != key_of(raw, self.key_override):
                if new_key == raw["key"]:
                    self.key_override.pop(row_id, None)
                else:
                    self.key_override[row_id] = new_key
                bldg_changed = True
                substantive = True
        except Exception:
            pass

        try:
            new_disc = str(row["Disc"])
            if new_disc != disc_of(raw, self.disc_override):
                if new_disc == raw["disc"]:
                    self.disc_override.pop(row_id, None)
                else:
                    self.disc_override[row_id] = new_disc
                self._prune_stale_refs()
                substantive = True
        except Exception:
            pass

        try:
            new_ref = int(row["RefId"])
            if new_ref != self.refs.get(row_id, -1):
                if new_ref == -1:
                    self.refs.pop(row_id, None)
                else:
                    self.refs[row_id] = new_ref
                substantive = True
        except Exception:
            pass

        try:
            new_included = bool(row["Included"])
            currently_included = not self.excluded.get(row_id, False)
            if new_included != currently_included:
                self.excluded[row_id] = not new_included
                substantive = True
                # This row is part of a multi-row selection: ticking/
                # unticking ITS OWN Included checkbox applies the same value
                # to every other selected row too -- select several rows,
                # then flip any one of their own checkboxes to affect the
                # whole selection at once (in addition to the dedicated
                # bulk-edit bar's explicit "Apply" flow).
                if row_id in self._selected_ids and len(self._selected_ids) > 1:
                    for other_id in self._selected_ids:
                        if other_id != row_id and other_id != 0:
                            self.excluded[other_id] = not new_included
        except Exception:
            pass

        if bldg_changed:
            # The building key drives auto-matching -- re-derive just THIS
            # row's own Reference (AR) from the current AR set now that its
            # key changed. Other rows' refs (already auto- or hand-matched)
            # are left exactly as they are.
            fresh_refs = auto_match_references(self.raw_rows, self.disc_override, self.key_override)
            if row_id in fresh_refs:
                self.refs[row_id] = fresh_refs[row_id]
            else:
                self.refs.pop(row_id, None)

        # Defer so this handler returns before we touch the DataTable again
        # (avoids re-entering RowChanged mid-dispatch).
        if substantive:
            self._window.Dispatcher.BeginInvoke(System.Action(self._refresh_results_grid))
        else:
            self._window.Dispatcher.BeginInvoke(System.Action(self._refresh_bulk_bar))

    def _on_select_all_click(self, sender, args):
        resolved = self._resolved()
        visible_ids = [r["id"] for r in resolved if r["id"] != 0]
        if sender.IsChecked:
            self._selected_ids |= set(visible_ids)
        else:
            self._selected_ids -= set(visible_ids)
        self._refresh_results_grid()

    _BULK_FIELDS = [("disc", u"Disc"), ("ref", u"Reference (AR)"), ("included", u"Included in report")]

    def _refresh_bulk_bar(self):
        bar = self._bulk_bar
        bar.Children.Clear()
        n = len(self._selected_ids)
        if n == 0:
            self._bulk_card.Visibility = System.Windows.Visibility.Collapsed
            return
        self._bulk_card.Visibility = System.Windows.Visibility.Visible

        row1 = _hbox()
        row1.Margin = System.Windows.Thickness(0, 0, 0, 7)
        label = _tb(u"{} row{} selected — editing any field below applies to all of them at once".format(
            n, "" if n == 1 else "s"), size=12, color=NAVY, bold=True)
        label.VerticalAlignment = System.Windows.VerticalAlignment.Center
        row1.Children.Add(label)
        clear_btn = WC.Button()
        clear_btn.Content = u"Clear selection"
        clear_btn.Style = self._window.FindResource("GhostBtn")
        clear_btn.Margin = System.Windows.Thickness(10, 0, 0, 0)
        def on_clear(s, e):
            self._selected_ids.clear()
            self._refresh_results_grid()
        clear_btn.Click += on_clear
        row1.Children.Add(clear_btn)
        bar.Children.Add(row1)

        row2 = _hbox()
        row2.Children.Add(_tb(u"Set", size=12.5, color="#374151", margin=(0, 0, 6, 0)))

        field_combo = WC.ComboBox()
        field_combo.Width = 150
        for _fkey, flabel in self._BULK_FIELDS:
            field_combo.Items.Add(flabel)
        field_combo.SelectedIndex = [k for k, _l in self._BULK_FIELDS].index(self._bulk_field)

        value_host = _hbox()
        value_host.Margin = System.Windows.Thickness(6, 0, 6, 0)

        def build_value_editor():
            value_host.Children.Clear()
            if self._bulk_field == "disc":
                combo = WC.ComboBox()
                combo.Width = 90
                codes = [c for c in self._all_disc_codes() if c != "-"]  # "-" is HOST-only, never a real edit target
                for c in codes:
                    combo.Items.Add(c)
                if getattr(self, "_bulk_disc_value", None) not in codes:
                    self._bulk_disc_value = codes[0]
                combo.SelectedItem = self._bulk_disc_value
                def on_change(s, e):
                    self._bulk_disc_value = s.SelectedItem
                combo.SelectionChanged += on_change
                value_host.Children.Add(combo)
            elif self._bulk_field == "ref":
                combo = WC.ComboBox()
                combo.Width = 220
                options = [(int(r["Id"]), r["Label"]) for r in self._ref_table.Rows]
                combo.ItemsSource = NetList[System.String]([lbl for _id, lbl in options])
                cur_val = getattr(self, "_bulk_ref_value", -1)
                cur_label = next((lbl for rid, lbl in options if rid == cur_val), options[0][1] if options else u"")
                combo.SelectedItem = cur_label
                def on_change(s, e):
                    idx = combo.SelectedIndex
                    self._bulk_ref_value = options[idx][0] if 0 <= idx < len(options) else -1
                combo.SelectionChanged += on_change
                value_host.Children.Add(combo)
            else:  # included
                combo = WC.ComboBox()
                combo.Width = 90
                combo.Items.Add(u"Yes")
                combo.Items.Add(u"No")
                combo.SelectedItem = u"Yes" if getattr(self, "_bulk_included_value", True) else u"No"
                def on_change(s, e):
                    self._bulk_included_value = (combo.SelectedItem == u"Yes")
                combo.SelectionChanged += on_change
                value_host.Children.Add(combo)

        build_value_editor()

        def on_field_change(s, e):
            self._bulk_field = self._BULK_FIELDS[field_combo.SelectedIndex][0]
            build_value_editor()
        field_combo.SelectionChanged += on_field_change

        row2.Children.Add(field_combo)
        row2.Children.Add(_tb(u"to", size=12.5, color="#374151", margin=(0, 0, 0, 0)))
        row2.Children.Add(value_host)

        apply_btn = WC.Button()
        apply_btn.Content = u"Apply to {}".format(n)
        apply_btn.Style = self._window.FindResource("PrimaryBtn")
        apply_btn.Click += self._on_apply_bulk_edit
        row2.Children.Add(apply_btn)
        bar.Children.Add(row2)

    def _on_apply_bulk_edit(self, sender, args):
        try:
            self._apply_bulk_edit_impl()
        except Exception:
            logger.error(traceback.format_exc())
            System.Windows.MessageBox.Show(
                u"Bulk edit failed:\n\n" + traceback.format_exc(), u"Project Base Points",
                System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error)

    def _apply_bulk_edit_impl(self):
        ids = [rid for rid in self._selected_ids if rid != 0]  # HOST is never editable
        if not ids:
            return
        raw_by_id = dict((r["id"], r) for r in self.raw_rows)
        if self._bulk_field == "disc":
            value = getattr(self, "_bulk_disc_value", None)
            for rid in ids:
                raw = raw_by_id.get(rid)
                if raw is None:
                    continue
                if value == raw["disc"]:
                    self.disc_override.pop(rid, None)
                else:
                    self.disc_override[rid] = value
            self._prune_stale_refs()
        elif self._bulk_field == "ref":
            value = getattr(self, "_bulk_ref_value", -1)
            for rid in ids:
                if value == -1:
                    self.refs.pop(rid, None)
                else:
                    self.refs[rid] = value
        else:  # included
            value = getattr(self, "_bulk_included_value", True)
            for rid in ids:
                self.excluded[rid] = not value
        self._refresh_results_grid()

    def _rebuild_chips(self, resolved):
        row = self._chip_row
        row.Children.Clear()
        counts = {"all": 0, "bad": 0, "unshared": 0, "unresolved": 0}
        for r in resolved:
            if r["kind"] == "HOST" or r["status"] == "Reference" or not r["included"]:
                continue
            counts["all"] += 1
            if r["status"] == "Not OK":
                counts["bad"] += 1
            elif r["status"] == "Not Shared":
                counts["unshared"] += 1
            elif r["status"] in ("Missing Ref", "Unloaded"):
                counts["unresolved"] += 1

        def make_chip(key, label):
            b = WC.Button()
            on = self.filter == key
            b.Content = u"{} ({})".format(label, counts[key])
            b.Padding = System.Windows.Thickness(11, 5, 11, 5)
            b.Margin = System.Windows.Thickness(0, 0, 7, 0)
            b.FontSize = 12
            b.Cursor = System.Windows.Input.Cursors.Hand
            b.Background = WM.BrushConverter().ConvertFromString(NAVY if on else "#fff")
            b.Foreground = WM.BrushConverter().ConvertFromString("#fff" if on else "#4b5563")
            b.BorderBrush = WM.BrushConverter().ConvertFromString(NAVY if on else "#e6e8f5")
            b.BorderThickness = System.Windows.Thickness(1)
            def click(s, e, k=key):
                self.filter = k
                self._refresh_results_grid()
            b.Click += click
            return b

        row.Children.Add(make_chip("all", "All rows"))
        row.Children.Add(make_chip("bad", "Not coordinated"))
        row.Children.Add(make_chip("unshared", "Not shared"))
        row.Children.Add(make_chip("unresolved", "Unresolved"))

        reset_btn = WC.Button()
        reset_btn.Content = u"Reset mapping"
        reset_btn.Style = self._window.FindResource("GhostBtn")
        reset_btn.Margin = System.Windows.Thickness(14, 0, 0, 0)
        def on_reset(s, e):
            self.refs = auto_match_references(self.raw_rows)
            self.disc_override = {}
            self.key_override = {}
            self.excluded = {}
            self._refresh_results_grid()
        reset_btn.Click += on_reset
        row.Children.Add(reset_btn)

        hint = _tb(u"·  tick the ✓ boxes in the Sel column to bulk-edit or bulk-include/exclude several rows at once",
                   size=10.5, color="#9aa0ac", wrap=True)
        hint.Margin = System.Windows.Thickness(12, 0, 0, 0)
        hint.VerticalAlignment = System.Windows.VerticalAlignment.Center
        row.Children.Add(hint)

    def _apply_results_filter(self):
        view = self._results_table.DefaultView
        clauses = []
        if self.filter == "bad":
            clauses.append("Status = 'Not OK'")
        elif self.filter == "unshared":
            clauses.append("Status = 'Not Shared'")
        elif self.filter == "unresolved":
            clauses.append("(Status = 'Missing Ref' OR Status = 'Unloaded')")
        view.RowFilter = " AND ".join(clauses) if clauses else ""

    def _rebuild_chart(self, resolved):
        panel = self._chart_panel
        panel.Children.Clear()
        # Grouped by HEBREW LABEL, not raw code -- HEB_OPTIONS is a small,
        # fixed taxonomy that several discipline codes deliberately share
        # (e.g. "ST" Structural and "C" Geotechnical both read "קונסטרוקציה"),
        # so grouping by code showed two bars with identical-looking text.
        groups = {}
        codes_by_group = {}
        for r in resolved:
            if r["kind"] == "HOST" or r["status"] == "Reference" or not r["included"]:
                continue
            code = r["disc"] or "?"
            d = auto_heb(code)
            g = groups.setdefault(d, {"ok": 0, "notok": 0, "unshared": 0, "other": 0, "total": 0})
            g["total"] += 1
            key = {"OK": "ok", "Not OK": "notok", "Not Shared": "unshared"}.get(r["status"], "other")
            g[key] += 1
            codes_by_group.setdefault(d, set()).add(code)
        if not groups:
            return
        # Fixed, compact total width (not a Star column stretching across the
        # whole dialog) -- a Star column here left the actual colored bar a
        # fixed 260px while the count text got pushed to the far right edge
        # of the dialog, making each row look stretched/too wide to read.
        BAR_PX = 170
        card = _card()
        card.Padding = System.Windows.Thickness(12, 9, 12, 9)
        card.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
        stk = WC.StackPanel()
        for d in sorted(groups.keys()):
            g = groups[d]
            gr = WC.Grid()
            gr.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
            for w_ in (72, BAR_PX, 52):
                cd = WC.ColumnDefinition()
                cd.Width = System.Windows.GridLength(w_)
                gr.ColumnDefinitions.Add(cd)
            gr.Margin = System.Windows.Thickness(0, 0, 0, 5)
            dtb = _tb(d, size=11, color=NAVY, bold=True)
            dtb.FlowDirection = System.Windows.FlowDirection.RightToLeft
            dtb.TextAlignment = System.Windows.TextAlignment.Right
            dtb.VerticalAlignment = System.Windows.VerticalAlignment.Center
            dtb.Margin = System.Windows.Thickness(0, 0, 8, 0)
            # which discipline code(s) this Hebrew bucket actually covers
            dtb.ToolTip = u", ".join(sorted(codes_by_group.get(d, [])))
            gr.Children.Add(dtb)
            bar = _hbox()
            bar.Height = 13
            total = float(g["total"]) or 1.0
            for key, color in [("ok", OK_C), ("notok", ORANGE), ("unshared", RED), ("other", "#c9cedb")]:
                seg = WC.Border()
                seg.Width = (g[key] / total) * float(BAR_PX)
                seg.Background = WM.BrushConverter().ConvertFromString(color)
                bar.Children.Add(seg)
            WC.Grid.SetColumn(bar, 1)
            gr.Children.Add(bar)
            ntb = _tb(u"{} / {}".format(g["ok"], g["total"]), size=11, color="#9aa0ac")
            ntb.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            ntb.VerticalAlignment = System.Windows.VerticalAlignment.Center
            WC.Grid.SetColumn(ntb, 2)
            gr.Children.Add(ntb)
            stk.Children.Add(gr)
        card.Child = stk
        panel.Children.Add(card)

    # =========================================================== EXPORT
    def _refresh_export_panel(self):
        p = self._export_panel
        p.Children.Clear()
        resolved = self._resolved()
        not_ok = [r for r in resolved if r["included"] and r["status"] == "Not OK"]
        unshared = [r for r in resolved if r["included"] and r["status"] == "Not Shared"]
        unres = [r for r in resolved if r["included"] and r["status"] == "Missing Ref"]

        p.Children.Add(_section_label(u"Formats · pick any combination"))
        for fid, name, ext, desc in FORMATS:
            on = self.exp["formats"].get(fid, False)
            card = WC.Border()
            card.BorderBrush = WM.BrushConverter().ConvertFromString(CYAN if on else "#e6e8f5")
            card.BorderThickness = System.Windows.Thickness(1.5)
            card.Background = WM.BrushConverter().ConvertFromString("#F0FAFC" if on else "#fff")
            card.CornerRadius = System.Windows.CornerRadius(10)
            card.Padding = System.Windows.Thickness(13)
            card.Margin = System.Windows.Thickness(0, 0, 0, 8)
            card.Cursor = System.Windows.Input.Cursors.Hand
            stk = WC.StackPanel()
            top = _hbox()
            chk = WC.Border()
            chk.Width = chk.Height = 17
            chk.CornerRadius = System.Windows.CornerRadius(5)
            chk.Background = WM.BrushConverter().ConvertFromString(CYAN if on else "#fff")
            chk.BorderBrush = WM.BrushConverter().ConvertFromString(CYAN if on else "#d5d9e6")
            chk.BorderThickness = System.Windows.Thickness(1.5)
            if on:
                mark = _tb(u"✓", size=11, color="#fff", bold=True)
                mark.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
                chk.Child = mark
            top.Children.Add(chk)
            name_tb = _tb(name, size=13.5, color=NAVY if on else "#374151", bold=True)
            name_tb.Margin = System.Windows.Thickness(10, 0, 0, 0)
            top.Children.Add(name_tb)
            stk.Children.Add(top)
            desc_tb = _tb(desc, size=11.5, color="#6b7280", wrap=True)
            desc_tb.Margin = System.Windows.Thickness(27, 4, 0, 0)
            stk.Children.Add(desc_tb)
            card.Child = stk

            def toggle(s, e, key=fid):
                self.exp["formats"][key] = not self.exp["formats"].get(key, False)
                self._refresh_export_panel()
                self._refresh_footer()
            card.MouseLeftButtonUp += toggle
            p.Children.Add(card)

        has_doc = self.exp["formats"].get("html") or self.exp["formats"].get("pdf")
        has_xlsx = self.exp["formats"].get("xlsx")

        p.Children.Add(_section_label(u"Contents of the HTML / print report"))
        opt_card = _card()
        opt_stk = WC.StackPanel()
        opt_stk.Margin = System.Windows.Thickness(14, 10, 14, 10)
        opt_stk.Opacity = 1.0 if has_doc else 0.5

        def opt_toggle(key):
            def handler(s, e):
                self.exp[key] = bool(s.IsChecked)
                self._refresh_footer()
            return handler
        opt_defs = [
            ("onlyIssues", u"Only rows needing attention",
             u"{} not coordinated · {} not shared".format(len(not_ok), len(unshared))),
            ("includeUnresolved", u"Include Missing Ref rows",
             u"{} row(s) with no Architecture reference — never exported as issues".format(len(unres))),
            ("chart", u"Include the coordination chart", u"Stacked bar per discipline"),
            ("open", u"Open the files when finished", u"Launches each report in its default application"),
        ]
        for key, label, hint in opt_defs:
            row_stk = WC.StackPanel()
            row_stk.Margin = System.Windows.Thickness(0, 2, 0, 8)
            row_stk.Children.Add(_checkbox(label, self.exp.get(key, False), opt_toggle(key)))
            h = _tb(hint, size=10.5, color="#9aa0ac", wrap=True)
            h.Margin = System.Windows.Thickness(22, 0, 0, 0)
            row_stk.Children.Add(h)
            opt_stk.Children.Add(row_stk)
        opt_card.Child = opt_stk
        p.Children.Add(opt_card)

        if has_xlsx:
            note = _tb(u"The issue sheet always carries only the {} flagged link(s), whatever is set above.".format(
                len(not_ok) + len(unshared)), size=11, color="#9aa0ac", wrap=True)
            note.Margin = System.Windows.Thickness(2, 6, 2, 0)
            p.Children.Add(note)

        p.Children.Add(_section_label(u"Destination · remembered for next run"))
        dest_card = _card()
        dest_row = WC.Grid()
        dest_row.Margin = System.Windows.Thickness(10, 6, 10, 6)
        dest_row.ColumnDefinitions.Add(WC.ColumnDefinition())
        dest_row.ColumnDefinitions[0].Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        dest_row.ColumnDefinitions.Add(WC.ColumnDefinition())
        folder_box = WC.TextBox()
        folder_box.Text = self.exp["folder"]
        folder_box.FontFamily = WM.FontFamily("Consolas")
        folder_box.BorderThickness = System.Windows.Thickness(0)
        folder_box.VerticalContentAlignment = System.Windows.VerticalAlignment.Center
        def on_folder_change(s, e):
            self.exp["folder"] = s.Text
        folder_box.TextChanged += on_folder_change
        dest_row.Children.Add(folder_box)
        browse_btn = WC.Button()
        browse_btn.Content = u"Browse…"
        browse_btn.Style = self._window.FindResource("GhostBtn")
        browse_btn.Click += self._on_browse_folder
        WC.Grid.SetColumn(browse_btn, 1)
        dest_row.Children.Add(browse_btn)
        dest_card.Child = dest_row
        p.Children.Add(dest_card)
        subfolder_note = _tb(u"Each export writes into its own dated subfolder here, e.g. "
                             u"...\\{}\\ -- re-running never overwrites a previous run.".format(
                                 self._run_folder_name()),
                             size=10.5, color="#9aa0ac", wrap=True)
        subfolder_note.Margin = System.Windows.Thickness(2, 5, 2, 0)
        p.Children.Add(subfolder_note)

        picks = [(fid, ext) for fid, _n, ext, _d in FORMATS if self.exp["formats"].get(fid)]
        if not picks:
            warn = _tb(u"pick at least one format", size=12, color=ORANGE)
            warn.FontFamily = WM.FontFamily("Consolas")
            warn.Margin = System.Windows.Thickness(2, 10, 0, 0)
            p.Children.Add(warn)
        else:
            base = self._file_base()
            for fid, ext in picks:
                n_rows = (len(not_ok) + len(unshared)) if fid == "xlsx" else self._doc_scope_count(resolved)
                line = _tb(u"{}{}  · {} row{}".format(base, ext, n_rows, "" if n_rows == 1 else "s"),
                           size=12, color="#6b7280")
                line.FontFamily = WM.FontFamily("Consolas")
                line.Margin = System.Windows.Thickness(2, 4, 0, 0)
                p.Children.Add(line)

    def _doc_scope_count(self, resolved):
        included = sum(1 for r in resolved if r["kind"] == "HOST" or r["included"])
        if not self.exp.get("onlyIssues"):
            return included
        not_ok = sum(1 for r in resolved if r["included"] and r["status"] == "Not OK")
        unshared = sum(1 for r in resolved if r["included"] and r["status"] == "Not Shared")
        unres = sum(1 for r in resolved if r["included"] and r["status"] == "Missing Ref") if self.exp.get("includeUnresolved") else 0
        return not_ok + unshared + unres

    def _on_browse_folder(self, sender, args):
        import System.Windows.Forms as WF
        dlg = WF.FolderBrowserDialog()
        dlg.SelectedPath = self.exp["folder"] if os.path.isdir(self.exp["folder"]) else os.path.expanduser("~")
        if dlg.ShowDialog() == WF.DialogResult.OK:
            self.exp["folder"] = dlg.SelectedPath
            self._refresh_export_panel()

    def _file_base(self, now=None):
        now = now or datetime.datetime.now()
        return u"{}_PBP_Report_{}".format(_sanitize_filename(self.host_info["title"]), now.strftime("%Y-%m-%d_%H%M"))

    def _run_folder_name(self, now=None):
        """Each export run gets its own dated subfolder under the remembered
        destination, so re-running the tool against the same destination
        never overwrites a previous run's files."""
        now = now or datetime.datetime.now()
        return now.strftime("%d-%m-%Y - %H%M")

    # =========================================================== ISSUES
    def _cf_fields(self):
        if not self.opts.get("notHub"):
            return [f for f, _role in HUB_FIELDS]
        a = self.acc
        if a["cfMode"] == "manual":
            return [f.strip() for f in (a["cfText"] or "").split(",") if f.strip()]
        if a["cfMode"] == "import":
            return list(a.get("cfList") or [])
        return []

    def _cf_role_for(self, field):
        if not self.opts.get("notHub"):
            return dict(HUB_FIELDS).get(field, "")
        return self.acc["cfRole"].get(field, "")

    def _issue_rows_current(self):
        resolved = self._resolved()
        base = pbp_report_xlsx.build_issue_rows(resolved, self.acc, self.tol_mm, self.tol_deg)
        return pbp_report_xlsx.apply_row_edits(base, self.acc)

    def _refresh_issues_panel(self):
        p = self._issues_panel
        p.Children.Clear()
        a = self.acc

        p.Children.Add(_section_label(u"Custom fields on this hub"))
        cf_card = _card()
        cf_stk = WC.StackPanel()
        cf_stk.Margin = System.Windows.Thickness(14, 12, 14, 12)

        if not self.opts.get("notHub"):
            # On the EasyBIM Hub these two custom fields are guaranteed to
            # exist on every project's ACC issue-import template -- nothing
            # to configure. The None/Manual/Import wizard below only makes
            # sense for a project that ISN'T on the hub (no fixed fields to
            # assume), so it's skipped entirely here.
            info = _tb(u"This project is on the EasyBIM Hub, which always has these two custom fields "
                       u"configured -- nothing to set up here:", size=12, color="#374151", wrap=True)
            cf_stk.Children.Add(info)
            for field, role in HUB_FIELDS:
                frow = _hbox()
                frow.Margin = System.Windows.Thickness(0, 8, 0, 0)
                ftag = _tb(field, size=11.5, color=NAVY, bold=True)
                ftag.FontFamily = WM.FontFamily("Consolas")
                ftag.Width = 140
                frow.Children.Add(ftag)
                frow.Children.Add(_tb(u"→", size=12, color=CYAN, margin=(0, 0, 8, 0)))
                frow.Children.Add(_tb(u"דיספלינה (Hebrew discipline)" if role == "discipline" else u"building key",
                                      size=12, color="#6b7280"))
                cf_stk.Children.Add(frow)
            note = _tb(u"If this project actually isn't on the EasyBIM Hub, tick \"Not in EasyBIM Hub\" on the "
                      u"Setup screen to configure your own custom fields instead.",
                      size=10.5, color="#9aa0ac", wrap=True)
            note.Margin = System.Windows.Thickness(0, 10, 0, 0)
            cf_stk.Children.Add(note)
        else:
            modes_row = WC.Grid()
            for _ in range(3):
                modes_row.ColumnDefinitions.Add(WC.ColumnDefinition())
            for i, (mid, label) in enumerate([("none", u"No custom fields"), ("manual", u"Type them manually"),
                                               ("import", u"Import a report example")]):
                on = a["cfMode"] == mid
                btn = WC.Button()
                btn.Content = label
                btn.Margin = System.Windows.Thickness(0 if i == 0 else 6, 0, 0, 0)
                btn.Padding = System.Windows.Thickness(8)
                btn.Background = WM.BrushConverter().ConvertFromString("#F0FAFC" if on else "#fff")
                btn.BorderBrush = WM.BrushConverter().ConvertFromString(CYAN if on else "#e6e8f5")
                btn.Foreground = WM.BrushConverter().ConvertFromString(NAVY if on else "#4b5563")
                btn.BorderThickness = System.Windows.Thickness(1.5)
                def on_mode(s, e, m=mid):
                    a["cfMode"] = m
                    a["cfText"], a["cfList"], a["cfRole"] = "", [], {}
                    self._refresh_issues_panel()
                btn.Click += on_mode
                WC.Grid.SetColumn(btn, i)
                modes_row.Children.Add(btn)
            cf_stk.Children.Add(modes_row)

            if a["cfMode"] == "manual":
                lbl = _tb(u"Field names · comma separated, spelled exactly as configured in the project",
                          size=10.5, color="#9aa0ac", wrap=True)
                lbl.Margin = System.Windows.Thickness(0, 10, 0, 4)
                cf_stk.Children.Add(lbl)
                box = WC.TextBox()
                box.Text = a["cfText"]
                box.AcceptsReturn = False
                box.FontFamily = WM.FontFamily("Consolas")
                box.Padding = System.Windows.Thickness(8)
                def on_cf_text(s, e):
                    text = s.Text
                    a["cfText"] = text
                    fields = [f.strip() for f in text.split(",") if f.strip()]
                    new_role = dict(self._auto_cf_role(fields))
                    for f in fields:
                        if a["cfRole"].get(f):
                            new_role[f] = a["cfRole"][f]
                    a["cfRole"] = new_role
                    self._refresh_issues_panel()
                box.LostFocus += on_cf_text
                cf_stk.Children.Add(box)
            elif a["cfMode"] == "import":
                lbl = _tb(u"Issue report exported from the target project", size=10.5, color="#9aa0ac")
                lbl.Margin = System.Windows.Thickness(0, 10, 0, 4)
                cf_stk.Children.Add(lbl)
                row_ = _hbox()
                path_box = WC.TextBox()
                path_box.Width = 320
                path_box.Text = a.get("template", "")
                def on_template(s, e):
                    a["template"] = s.Text
                path_box.TextChanged += on_template
                row_.Children.Add(path_box)
                browse = WC.Button()
                browse.Content = u"Browse…"
                browse.Style = self._window.FindResource("GhostBtn")
                browse.Margin = System.Windows.Thickness(8, 0, 0, 0)
                def on_browse_template(s, e):
                    import System.Windows.Forms as WF
                    dlg = WF.OpenFileDialog()
                    dlg.Filter = "Excel files (*.xlsx)|*.xlsx"
                    if dlg.ShowDialog() == WF.DialogResult.OK:
                        a["template"] = dlg.FileName
                        a["cfList"] = self._read_xlsx_headers(dlg.FileName)
                        a["cfRole"] = self._auto_cf_role(a["cfList"])
                        self._refresh_issues_panel()
                browse.Click += on_browse_template
                row_.Children.Add(browse)
                cf_stk.Children.Add(row_)

            fields = self._cf_fields()
            if fields:
                map_lbl = _tb(u"Map them · דיספלינה and Location match "
                              u"automatically where the name allows", size=10.5, color="#9aa0ac", wrap=True)
                map_lbl.Margin = System.Windows.Thickness(0, 10, 0, 4)
                cf_stk.Children.Add(map_lbl)
                for f in fields:
                    frow = _hbox()
                    frow.Margin = System.Windows.Thickness(0, 3, 0, 3)
                    ftag = _tb(f, size=11, color=NAVY, bold=True)
                    ftag.FontFamily = WM.FontFamily("Consolas")
                    ftag.Width = 160
                    frow.Children.Add(ftag)
                    role_combo = WC.ComboBox()
                    role_combo.Width = 200
                    role_combo.Items.Add(u"— fill per issue below —")
                    role_combo.Items.Add(u"דיספלינה")
                    role_combo.Items.Add(u"Location")
                    cur = a["cfRole"].get(f, "")
                    role_combo.SelectedIndex = {"": 0, "discipline": 1, "building": 2}.get(cur, 0)
                    def on_role(s, e, field=f):
                        idx = s.SelectedIndex
                        val = {0: "", 1: "discipline", 2: "building"}[idx]
                        if val:
                            for k in list(a["cfRole"].keys()):
                                if a["cfRole"][k] == val:
                                    a["cfRole"][k] = ""
                        a["cfRole"][field] = val
                        self._refresh_issues_panel()
                    role_combo.SelectionChanged += on_role
                    frow.Children.Add(role_combo)
                    cf_stk.Children.Add(frow)
            elif a["cfMode"] != "none":
                cf_stk.Children.Add(_tb(u"No fields configured yet.", size=11, color="#9aa0ac"))
        cf_card.Child = cf_stk
        p.Children.Add(cf_card)

        # Issue defaults
        p.Children.Add(_section_label(u"Issue defaults · must match the values configured in the project"))
        defaults_card = _card()
        dgrid = WC.WrapPanel()
        dgrid.Margin = System.Windows.Thickness(14, 12, 14, 12)

        def field_box(label, key, width=170, mono=False):
            stk = WC.StackPanel()
            stk.Margin = System.Windows.Thickness(0, 0, 10, 10)
            stk.Width = width
            stk.Children.Add(_tb(label.upper(), size=9, color="#9aa0ac"))
            box = WC.TextBox()
            box.Text = str(a.get(key, ""))
            box.Padding = System.Windows.Thickness(7, 5, 7, 5)
            if mono:
                box.FontFamily = WM.FontFamily("Consolas")
            def on_change(s, e, k=key):
                a[k] = s.Text
                self._refresh_footer()
            box.TextChanged += on_change
            stk.Children.Add(box)
            return stk

        dgrid.Children.Add(field_box(u"Title · base point mismatch", "title", 220))
        dgrid.Children.Add(field_box(u"Title · not in shared coordinates", "titleShared", 220))
        dgrid.Children.Add(field_box(u"Status", "status"))
        dgrid.Children.Add(field_box(u"Category", "category"))
        dgrid.Children.Add(field_box(u"Type", "type"))
        dgrid.Children.Add(field_box(u"Due in (days)", "dueDays", 120, mono=True))

        at_stk = WC.StackPanel()
        at_stk.Margin = System.Windows.Thickness(0, 0, 10, 10)
        at_stk.Width = 170
        at_stk.Children.Add(_tb(u"ASSIGNEE TYPE", size=9, color="#9aa0ac"))
        at_combo = WC.ComboBox()
        at_combo.Items.Add("role")
        at_combo.Items.Add("company")
        at_combo.SelectedItem = a.get("assigneeType", "role")
        def on_at(s, e):
            a["assigneeType"] = s.SelectedItem
            self._refresh_issues_panel()
        at_combo.SelectionChanged += on_at
        at_stk.Children.Add(at_combo)
        dgrid.Children.Add(at_stk)

        defaults_card.Child = dgrid
        p.Children.Add(defaults_card)
        title_note = _tb(u"Each issue's actual title is \"{title} - {building}\" -- the building/zone key "
                         u"is appended automatically per link, so a batch of issues doesn't all read identically.",
                         size=10.5, color="#9aa0ac", wrap=True)
        title_note.Margin = System.Windows.Thickness(2, 6, 2, 0)
        p.Children.Add(title_note)

        # Assignee list
        is_company = a["assigneeType"] == "company"
        p.Children.Add(_section_label(
            (u"Company names on this hub" if is_company else u"Role names on this hub") +
            u" · comma separated, spelled exactly as in the project"))
        assignee_card = _card()
        astk = WC.StackPanel()
        astk.Margin = System.Windows.Thickness(14, 10, 14, 10)
        abox = WC.TextBox()
        abox.Text = a["companiesText"] if is_company else a["rolesText"]
        abox.AcceptsReturn = True
        abox.TextWrapping = System.Windows.TextWrapping.Wrap
        abox.Height = 50
        def on_assignee_text(s, e):
            key = "companiesText" if is_company else "rolesText"
            a[key] = s.Text
            self._refresh_footer()
        abox.LostFocus += on_assignee_text
        astk.Children.Add(abox)
        alist = self._assignee_list()
        if is_company:
            status = (u"{} company(ies) parsed".format(len(alist)) if alist
                      else u"required: with Assignee Type = company, Assigned To must be a company name")
        else:
            if a["rolesText"]:
                status = u"{} role(s) parsed".format(len(alist))
            elif self.opts.get("notHub"):
                status = u"Not in EasyBIM Hub — type your own role list, no built-in fallback"
            else:
                status = u"empty — using the EasyBIM hub role list ({} roles)".format(len(HUB_ROLES))
        astk.Children.Add(_tb(status, size=10.5, color=(OK_C if alist else ORANGE)))
        assignee_card.Child = astk
        p.Children.Add(assignee_card)

        # Descriptions
        p.Children.Add(_section_label(u"Descriptions · Hebrew, written into the Description column"))
        desc_card = _card()
        dstk = WC.StackPanel()
        dstk.Margin = System.Windows.Thickness(14, 10, 14, 10)

        def desc_area(label, key, note=None):
            dstk.Children.Add(_tb(label.upper(), size=9, color="#9aa0ac", margin=(0, 6, 0, 3)))
            box = WC.TextBox()
            box.Text = a[key]
            box.AcceptsReturn = True
            box.TextWrapping = System.Windows.TextWrapping.Wrap
            box.FlowDirection = System.Windows.FlowDirection.RightToLeft
            box.Height = 42
            box.Padding = System.Windows.Thickness(8, 6, 8, 6)
            def on_change(s, e, k=key):
                a[k] = s.Text
            box.LostFocus += on_change
            dstk.Children.Add(box)
            if note:
                n = _tb(note, size=10.5, color="#9aa0ac", wrap=True)
                n.Margin = System.Windows.Thickness(0, 3, 0, 0)
                dstk.Children.Add(n)

        desc_area(u"Not OK · wrong elevation", "descElev")
        desc_area(u"Not OK · wrong angle to north", "descAngle")
        desc_area(u"Not OK · both elevation and angle", "descBoth",
                  u"The right one is picked per link from what actually differs; measured gaps and the "
                  u"reference model name are appended automatically.")
        desc_area(u"Not Shared · link is not on the shared site", "descShared")
        desc_card.Child = dstk
        p.Children.Add(desc_card)

        # Per-discipline table
        issue_rows = self._issue_rows_current()
        discs_used = sorted(set(x["disc"] for x in issue_rows))
        # דיספלינה only matters if some custom field is actually mapped to
        # receive it -- ACC's own standard columns never carry it, so with
        # no such field the value would be computed here and then written
        # nowhere. On the EasyBIM Hub this is always true (the fixed
        # "Discipline" field); off the hub it depends on what's mapped.
        show_heb = any(self._cf_role_for(f) == "discipline" for f in self._cf_fields())
        p.Children.Add(_section_label(u"Per-discipline values · Role/דיספלינה fill "
                                      u"themselves from the code"))
        pd_card = _card()
        pd_stk = WC.StackPanel()
        pd_stk.Margin = System.Windows.Thickness(14, 10, 14, 10)

        head = _hbox()
        head.Margin = System.Windows.Thickness(0, 0, 0, 4)
        def head_cell(text, width, margin=None):
            t = _tb(text.upper(), size=9, color="#9aa0ac", bold=True)
            t.Width = width
            if margin:
                t.Margin = System.Windows.Thickness(*margin)
            head.Children.Add(t)
        head_cell(u"Code", 46)
        head_cell(u"Company" if is_company else u"Role", 180)
        if show_heb:
            head_cell(u"דיספלינה", 130, margin=(8, 0, 0, 0))
        head_cell(u"Issues", 70, margin=(10, 0, 0, 0))
        pd_stk.Children.Add(head)

        if not discs_used:
            pd_stk.Children.Add(_tb(u"No flagged links — nothing to assign.", size=11.5, color="#9aa0ac"))
        for d in discs_used:
            n = sum(1 for x in issue_rows if x["disc"] == d)
            drow = _hbox()
            drow.Margin = System.Windows.Thickness(0, 3, 0, 3)
            dtag = _tb(d, size=11.5, color=NAVY, bold=True)
            dtag.FontFamily = WM.FontFamily("Consolas")
            dtag.Width = 46
            drow.Children.Add(dtag)
            role_box = WC.ComboBox()
            role_box.Width = 180
            role_box.IsEditable = True
            role_box.ItemsSource = NetList[System.String](alist)
            if is_company:
                role_box.Text = a["companies"].get(d, "")
            else:
                role_box.Text = a["roles"].get(d, guess_role(d, alist))
            def on_role_change(s, e, disc=d):
                key = "companies" if is_company else "roles"
                a[key][disc] = s.Text
                self._refresh_footer()
            role_box.LostFocus += on_role_change
            role_box.SelectionChanged += on_role_change
            drow.Children.Add(role_box)
            if show_heb:
                heb_combo = WC.ComboBox()
                heb_combo.Width = 130
                heb_combo.FlowDirection = System.Windows.FlowDirection.RightToLeft
                for opt in HEB_OPTIONS:
                    heb_combo.Items.Add(opt)
                heb_combo.SelectedItem = a["heb"].get(d, auto_heb(d))
                heb_combo.Margin = System.Windows.Thickness(8, 0, 0, 0)
                def on_heb_change(s, e, disc=d):
                    a["heb"][disc] = s.SelectedItem
                heb_combo.SelectionChanged += on_heb_change
                drow.Children.Add(heb_combo)
            drow.Children.Add(_tb(u"{} issue(s)".format(n), size=11, color="#6b7280",
                                  margin=(10, 0, 0, 0)))
            pd_stk.Children.Add(drow)
        pd_card.Child = pd_stk
        p.Children.Add(pd_card)

        # Preview grid -- this IS the actual sheet ACC will receive; scroll
        # down to it and edit any cell directly before exporting.
        p.Children.Add(_section_label(u"Preview · {} issue{} · every cell below is editable".format(
            len(issue_rows), u"" if len(issue_rows) == 1 else u"s")))
        if not issue_rows:
            empty_card = _card()
            empty_card.Padding = System.Windows.Thickness(16)
            empty_card.Child = _tb(u"Nothing flagged in the current selection — no issues to preview yet.",
                                    size=12, color="#9aa0ac")
            p.Children.Add(empty_card)
        else:
            self._build_preview_grid(p, issue_rows)

        note = _tb(u"Columns written: " + u" · ".join(pbp_report_xlsx.ACC_COLS) +
                   u"".join(u" + " + f for f in self._cf_fields()) +
                   u". Location is always left empty — it resolves against ACC's own Locations "
                   u"breakdown structure; the building goes to a mapped custom field instead.",
                   size=10.5, color="#9aa0ac", wrap=True)
        note.Margin = System.Windows.Thickness(2, 8, 2, 0)
        p.Children.Add(note)

    def _auto_cf_role(self, fields):
        role = {}
        got_d = got_b = False
        for f in fields:
            n = f.lower()
            if not got_d and (u"disciplin" in n or u"trade" in n or u"דיספלינה" in f):
                role[f] = "discipline"; got_d = True; continue
            if not got_b and re.search(r"location|building|zone|area|block", n):
                role[f] = "building"; got_b = True; continue
            role[f] = ""
        return role

    def _read_xlsx_headers(self, path):
        try:
            import xlsxwriter  # noqa: F401 -- confirms xlsxwriter is present in this environment
        except Exception:
            pass
        try:
            import clr as _clr
            _clr.AddReference('Microsoft.Office.Interop.Excel')
        except Exception:
            pass
        # No xlsx *reader* is available in this environment (xlsxwriter is
        # write-only) -- best-effort: try openpyxl if the user's Python has it,
        # else ask them to type the field names manually instead.
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb.active
            headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value]
            return [unicode(h) for h in headers]
        except Exception:
            logger.warning("Could not read headers from %s (no xlsx reader available); "
                            "type the custom field names manually instead.", path)
            return []

    def _assignee_list(self):
        a = self.acc
        if a["assigneeType"] == "company":
            return [x.strip() for x in (a["companiesText"] or "").split(",") if x.strip()]
        if a["rolesText"]:
            return [x.strip() for x in a["rolesText"].split(",") if x.strip()]
        return [] if self.opts.get("notHub") else list(HUB_ROLES)

    def _build_preview_grid(self, parent, issue_rows):
        std_cols = [("Title", "Title", 160), ("Description", "Description", 260), ("Status", "Status", 80),
                    ("Assigned To", "Assigned To", 130), ("Category", "Category", 90), ("Type", "Type", 100),
                    ("Start Date", "Start Date", 96), ("Due Date", "Due Date", 96)]
        fields = self._cf_fields()

        dt = SD.DataTable("Preview")
        dt.Columns.Add("Id", int)
        dt.Columns.Add("Discipline", str)
        dt.Columns.Add("Building", str)
        for f in fields:
            dt.Columns.Add(self._safe_col_name(f), str)
        for _label, key, _w in std_cols:
            dt.Columns.Add(self._safe_col_name(key), str)

        for x in issue_rows:
            vals = [x["id"], x.get("discipline", ""), x.get("building", "")]
            for f in fields:
                role = self._cf_role_for(f)
                if role == "discipline":
                    vals.append(x.get("discipline", ""))
                elif role == "building":
                    vals.append(x.get("building", ""))
                else:
                    vals.append((x.get("extra") or {}).get(f, ""))
            for _label, key, _w in std_cols:
                vals.append(x.get(key, ""))
            dt.Rows.Add(*vals)

        dt.RowChanged += self._on_preview_row_changed
        self._preview_table = dt
        self._preview_fields = fields

        grid = WC.DataGrid()
        grid.AutoGenerateColumns = False
        grid.CanUserAddRows = False
        grid.CanUserDeleteRows = False
        grid.RowHeaderWidth = 0
        grid.FontSize = 10.5
        grid.MinHeight = 90
        grid.MaxHeight = 340
        grid.GridLinesVisibility = WC.DataGridGridLinesVisibility.Horizontal
        grid.HorizontalGridLinesBrush = WM.BrushConverter().ConvertFromString("#eef0f7")
        grid.AlternatingRowBackground = WM.BrushConverter().ConvertFromString("#fafbff")

        heb_col = WC.DataGridTemplateColumn()
        heb_col.Header = u"דיספלינה"
        heb_col.CellTemplate = _parse_fragment(_HEB_CELL_XAML)
        heb_col.Width = WC.DataGridLength(110)
        grid.Columns.Add(heb_col)

        bcol = WC.DataGridTextColumn()
        bcol.Header = "Building"
        bcol.Binding = System.Windows.Data.Binding("Building")
        bcol.Width = WC.DataGridLength(90)
        grid.Columns.Add(bcol)

        for f in fields:
            col = WC.DataGridTextColumn()
            col.Header = f
            col.Binding = System.Windows.Data.Binding(self._safe_col_name(f))
            col.Width = WC.DataGridLength(120)
            grid.Columns.Add(col)
        for label, key, w in std_cols:
            col = WC.DataGridTextColumn()
            col.Header = label
            col.Binding = System.Windows.Data.Binding(self._safe_col_name(key))
            col.Width = WC.DataGridLength(w)
            grid.Columns.Add(col)

        grid.ItemsSource = dt.DefaultView
        card = _card()
        card.Child = grid
        parent.Children.Add(card)
        self._preview_grid_card = card

    def _safe_col_name(self, name):
        return "C_" + re.sub(r'\W+', '_', name)

    def _on_preview_row_changed(self, sender, e):
        if self._syncing:
            return
        row = e.Row
        row_id = int(row["Id"])
        row_edit = self.acc["rowEdit"].setdefault(row_id, {})
        extra = self.acc["extra"].setdefault(row_id, {})
        try:
            row_edit["discipline"] = str(row["Discipline"])
        except Exception:
            pass
        try:
            row_edit["building"] = str(row["Building"])
        except Exception:
            pass
        std_map = [("Title", "Title"), ("Description", "Description"), ("Status", "Status"),
                   ("Assigned To", "Assigned To"), ("Category", "Category"), ("Type", "Type"),
                   ("Start Date", "Start Date"), ("Due Date", "Due Date")]
        for label, key in std_map:
            try:
                row_edit[key] = str(row[self._safe_col_name(key)])
            except Exception:
                pass
        for f in self._preview_fields:
            role = self._cf_role_for(f)
            if role:
                continue
            try:
                extra[f] = str(row[self._safe_col_name(f)])
            except Exception:
                pass
        self._window.Dispatcher.BeginInvoke(System.Action(self._refresh_footer))

    # =========================================================== EXPORT EXECUTION
    def _on_export_click(self, sender, args):
        try:
            self._do_export()
        except Exception:
            logger.error(traceback.format_exc())
            System.Windows.MessageBox.Show(
                u"Export failed:\n\n" + traceback.format_exc(), u"Project Base Points",
                System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error)

    def _do_export(self):
        now = datetime.datetime.now()
        # Each run writes into its own "<DD-MM-YYYY - HHMM>" subfolder under
        # the remembered destination, so re-running against the same
        # destination never overwrites a previous run's files.
        folder = os.path.join(self.exp["folder"], self._run_folder_name(now))
        if not os.path.isdir(folder):
            os.makedirs(folder)
        base = self._file_base(now)
        resolved = self._resolved()
        generated_label = now.strftime("%Y-%m-%d %H:%M")

        # Unticking "Incl." for a link means exactly that -- it never shows
        # up in the report, regardless of the "only rows needing attention"
        # toggle below (that toggle narrows by STATUS on top of this).
        scoped = [r for r in resolved if r["kind"] == "HOST" or r["included"]]
        if self.exp.get("onlyIssues"):
            keep_status = set(ISSUE_STATUSES)
            if self.exp.get("includeUnresolved"):
                keep_status.add("Missing Ref")
            scoped = [r for r in scoped if r["kind"] == "HOST" or r["status"] in keep_status
                      or r["status"] in ("Reference",)]
        written = []

        show_chart = self.exp.get("chart", True)
        if self.exp["formats"].get("html"):
            out = os.path.join(folder, base + ".html")
            pbp_report_html.write_html(self.host_info, scoped, self.tol_mm, self.tol_deg, generated_label, out,
                                        show_chart=show_chart)
            written.append(("Interactive HTML", out))
        if self.exp["formats"].get("pdf"):
            out = os.path.join(folder, base + "_print.html")
            pbp_report_html.write_pdf(self.host_info, scoped, self.tol_mm, self.tol_deg, generated_label, out,
                                       show_chart=show_chart)
            written.append(("Print-ready HTML (Ctrl+P to save as PDF)", out))

        issue_count = 0
        if self.exp["formats"].get("xlsx"):
            issue_rows = self._issue_rows_current()
            issue_count = len(issue_rows)
            fields = self._cf_fields()
            custom_fields = [(f, self._cf_role_for(f) or None) for f in fields]
            out = os.path.join(folder, base + ".xlsx")
            pbp_report_xlsx.write_acc_issue_sheet(issue_rows, custom_fields, out)
            written.append(("Excel - ACC issue import", out))

        if self.opts.get("log"):
            import codecs
            log_path = os.path.join(folder, base + ".txt")
            with codecs.open(log_path, "w", "utf-8") as f:
                f.write(u"Project Base Points run log - {}\n".format(generated_label))
                f.write(u"Host: {}\n".format(self.host_info["title"]))
                f.write(u"Tolerance: {} mm / {} deg\n\n".format(self.tol_mm, self.tol_deg))
                for r in resolved:
                    status_txt = _status_display(r["status"], r.get("reason"))
                    f.write(u"{:<10} {:<32} disc={:<6} key={:<10} status={}\n".format(
                        r["kind"], r["link"], r["disc"], r["key"], status_txt))
            written.append(("Run log", log_path))

        if self.opts.get("remember"):
            self._save_all_prefs()

        if self.exp.get("open"):
            for _label, path in written:
                try:
                    os.startfile(path)
                except Exception:
                    logger.warning("Could not open %s", path)

        self._written_files = written
        self._last_issue_count = issue_count
        self._last_run_folder = folder
        self.cancelled = False
        self._go_to_stage("done")

    def _on_open_folder(self, sender, args):
        folder = getattr(self, "_last_run_folder", None) or self.exp["folder"]
        try:
            os.startfile(folder)
        except Exception:
            logger.warning("Could not open folder %s", folder)

    # =========================================================== DONE
    def _refresh_done_panel(self):
        p = self._done_panel
        p.Children.Clear()
        resolved = self._resolved()
        included = sum(1 for r in resolved if r["kind"] != "HOST" and r["included"])

        icon = _tb(u"✓", size=26, color=OK_C, bold=True)
        icon.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        p.Children.Add(icon)
        headline = _tb(u"{} file(s) written".format(len(self._written_files)), size=17, color=NAVY, bold=True)
        headline.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        headline.Margin = System.Windows.Thickness(0, 8, 0, 2)
        p.Children.Add(headline)
        sub = u"{} link(s) included".format(included)
        if self.exp["formats"].get("xlsx"):
            sub += u" · {} issue row(s)".format(self._last_issue_count)
        subtb = _tb(sub, size=12.5, color="#6b7280")
        subtb.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        subtb.Margin = System.Windows.Thickness(0, 0, 0, 16)
        p.Children.Add(subtb)

        list_card = _card()
        list_stk = WC.StackPanel()
        list_stk.Margin = System.Windows.Thickness(4)
        for label, path in self._written_files:
            row = WC.StackPanel()
            row.Margin = System.Windows.Thickness(10, 8, 10, 8)
            row.Children.Add(_tb(label, size=11, color="#9aa0ac"))
            ptb = _tb(path, size=12, color="#1f2937", bold=True)
            ptb.FontFamily = WM.FontFamily("Consolas")
            ptb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
            row.Children.Add(ptb)
            list_stk.Children.Add(row)
        list_card.Child = list_stk
        p.Children.Add(list_card)

        if self.exp["formats"].get("xlsx"):
            note = _tb(u"In Format/ACC open Issues ▸ ⋯ ▸ Import issues and pick this file. "
                       u"Any value the project doesn't recognise is highlighted in the import preview — "
                       u"come back to Issue fields and correct it, then export again.",
                       size=12, color="#374151", wrap=True)
            note.Margin = System.Windows.Thickness(2, 14, 2, 0)
            p.Children.Add(note)
