# -*- coding: utf-8 -*-
"""Shared WPF editor for Settings.json — SettingsDialog.

Used by "Coordination Graphics" (via its wizard's gear icon) as a nested
modal dialog. There is no standalone "Coordination Settings" pushbutton
anymore — the two tools were merged into one button per request; this
module is what makes the settings editor reachable from both without
duplicating ~300 lines of XAML.

Engine: IronPython 2.7 (no "#! python3" shebang) — matches the tab's other
WPF buttons. Callers are expected to have already done the WPF
clr.AddReference calls (PresentationFramework/Core, WindowsBase) — repeating
AddReference here is harmless (idempotent) so this module also does it
itself to stay independently usable.
"""

import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System')
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import System
import System.Windows.Media as WM

from Autodesk.Revit import DB
import Autodesk.Revit.Exceptions as RevitExceptions
from Autodesk.Revit.UI.Selection import ObjectType

from System.Windows.Markup import XamlReader
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from easybim import coordination_settings as cfgmod

XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="ARC/STR Settings"
  Width="520" Height="740"
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

    <Style x:Key="FieldLabel" TargetType="TextBlock">
      <Setter Property="FontSize"   Value="10.5"/>
      <Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Foreground" Value="#9aa0ac"/>
      <Setter Property="Margin"     Value="0,10,0,3"/>
    </Style>

    <Style x:Key="FieldBox" TargetType="TextBox">
      <Setter Property="Height"     Value="30"/>
      <Setter Property="FontSize"   Value="12"/>
      <Setter Property="Padding"    Value="8,4"/>
      <Setter Property="BorderBrush" Value="#dcdfef"/>
      <Setter Property="VerticalContentAlignment" Value="Center"/>
    </Style>

    <Style x:Key="SectionHeader" TargetType="TextBlock">
      <Setter Property="FontSize"   Value="13"/>
      <Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Foreground" Value="#1e248c"/>
      <Setter Property="Margin"     Value="0,0,0,2"/>
    </Style>

  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="72"/>
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
        <Border Grid.Column="0" Width="40" Height="40" CornerRadius="9" VerticalAlignment="Center"
                Background="#151b6e">
          <Grid Width="20" Height="20">
            <Ellipse Width="18" Height="18" Stroke="White" StrokeThickness="1.5"/>
            <Ellipse Width="7" Height="7" Fill="White"/>
          </Grid>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="ARC/STR Settings" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Keywords, concrete rules, patterns and colors"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="BtnCloseX" Grid.Column="2" Content="&#10005;" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- INFO BANNER -->
    <Border Grid.Row="1" Background="#ecf8fc" BorderBrush="#bbe5f0" BorderThickness="0,0,0,1" Padding="20,10">
      <StackPanel Orientation="Horizontal">
        <Border Width="18" Height="18" CornerRadius="9" Background="#44b8d3" VerticalAlignment="Top" Margin="0,1,10,0">
          <TextBlock Text="i" Foreground="White" FontSize="12" FontWeight="Bold"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock FontSize="11.5" Foreground="#1c6478" TextWrapping="Wrap" VerticalAlignment="Center" Width="440"
                   Text="Changes here take effect the next time you click Apply. Settings.json is shared with the team — commit it if you want your changes to stick."/>
      </StackPanel>
    </Border>

    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" Padding="20,14,20,10">
      <StackPanel>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="LINK DETECTION KEYWORDS" Style="{StaticResource SectionHeader}"/>
            <TextBlock Text="Comma-separated. A link is a candidate for a role if its name contains any of its keywords."
                       FontSize="10.5" Foreground="#8b93a7" TextWrapping="Wrap" Margin="0,2,0,0"/>

            <TextBlock Text="ARCHITECTURE LINK" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="ArchLinkKw" Style="{StaticResource FieldBox}"/>

            <TextBlock Text="STRUCTURE LINK" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="StructLinkKw" Style="{StaticResource FieldBox}"/>

            <TextBlock Text="TRAFFIC LINK" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="TrafficLinkKw" Style="{StaticResource FieldBox}"/>
          </StackPanel>
        </Border>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="CONCRETE CLASSIFICATION" Style="{StaticResource SectionHeader}"/>
            <TextBlock Text="A wall/column/framing/foundation TYPE counts as concrete if its material, name, description or type comments contain a concrete keyword and none of the exclude keywords."
                       FontSize="10.5" Foreground="#8b93a7" TextWrapping="Wrap" Margin="0,2,0,0"/>

            <TextBlock Text="CONCRETE KEYWORDS" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="ConcreteKw" Style="{StaticResource FieldBox}"/>

            <TextBlock Text="EXCLUDE KEYWORDS" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="ExcludeKw" Style="{StaticResource FieldBox}"/>
          </StackPanel>
        </Border>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="MANUAL EXCEPTIONS (BY TYPE NAME)" Style="{StaticResource SectionHeader}"/>
            <TextBlock TextWrapping="Wrap" FontSize="10.5" Foreground="#8b93a7" Margin="0,2,0,0"
                       Text="A Type Name listed in either box below ALWAYS overrides every rule above, in that direction -- for one specific type the keyword/material rules keep getting wrong."/>

            <TextBlock Text="ALWAYS TREAT AS CONCRETE" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="ManualIncludeTypes" Style="{StaticResource FieldBox}"/>
            <Button x:Name="BtnPickInclude" Content="Add from Model..." Style="{StaticResource GhostBtn}"
                    HorizontalAlignment="Left" Padding="14,0" Margin="0,8,0,0"/>

            <TextBlock Text="ALWAYS TREAT AS NON-CONCRETE" Style="{StaticResource FieldLabel}" Margin="0,16,0,3"/>
            <TextBox x:Name="ManualExcludeTypes" Style="{StaticResource FieldBox}"/>
            <Button x:Name="BtnPickExclude" Content="Add from Model..." Style="{StaticResource GhostBtn}"
                    HorizontalAlignment="Left" Padding="14,0" Margin="0,8,0,0"/>
          </StackPanel>
        </Border>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="FILL PATTERNS" Style="{StaticResource SectionHeader}"/>

            <TextBlock Text="STRUCTURE CUT HATCH PATTERN NAME" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="StructPattern" Style="{StaticResource FieldBox}"/>

            <TextBlock Text="ARCHITECTURE CUT HATCH PATTERN NAME" Style="{StaticResource FieldLabel}"/>
            <TextBox x:Name="ArchPattern" Style="{StaticResource FieldBox}"/>
          </StackPanel>
        </Border>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="COLORS" Style="{StaticResource SectionHeader}"/>

            <Grid Margin="0,10,0,0">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>

              <StackPanel Grid.Column="0" Margin="0,0,10,0">
                <TextBlock Text="STRUCTURE COLOR (R / G / B)" Style="{StaticResource FieldLabel}"/>
                <Grid>
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="26"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="StructR" Grid.Column="0" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <TextBox x:Name="StructG" Grid.Column="1" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <TextBox x:Name="StructB" Grid.Column="2" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <Border x:Name="StructSwatch" Grid.Column="3" CornerRadius="4" BorderBrush="#dcdfef" BorderThickness="1"/>
                </Grid>
              </StackPanel>

              <StackPanel Grid.Column="1" Margin="10,0,0,0">
                <TextBlock Text="ARCHITECTURE COLOR (R / G / B)" Style="{StaticResource FieldLabel}"/>
                <Grid>
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="26"/>
                  </Grid.ColumnDefinitions>
                  <TextBox x:Name="ArchR" Grid.Column="0" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <TextBox x:Name="ArchG" Grid.Column="1" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <TextBox x:Name="ArchB" Grid.Column="2" Style="{StaticResource FieldBox}" Margin="0,0,4,0"/>
                  <Border x:Name="ArchSwatch" Grid.Column="3" CornerRadius="4" BorderBrush="#dcdfef" BorderThickness="1"/>
                </Grid>
              </StackPanel>
            </Grid>
          </StackPanel>
        </Border>

        <TextBlock x:Name="ErrorText" Foreground="#d64545" FontSize="11.5" TextWrapping="Wrap"/>
        <TextBlock x:Name="PathText" Foreground="#9aa0ac" FontSize="10" TextWrapping="Wrap" Margin="0,10,0,0"/>

      </StackPanel>
    </ScrollViewer>

    <StackPanel Grid.Row="3" Orientation="Horizontal" HorizontalAlignment="Right" Margin="20,10,20,18">
      <Button x:Name="BtnReset"  Content="Reset to Defaults" Style="{StaticResource GhostBtn}" Width="140" Margin="0,0,10,0"/>
      <Button x:Name="BtnCancel" Content="Cancel"            Style="{StaticResource GhostBtn}" Width="90"  Margin="0,0,10,0"/>
      <Button x:Name="BtnSave"   Content="Save"               Style="{StaticResource PrimaryBtn}" Width="110"/>
    </StackPanel>
  </Grid>
</Window>
"""


def _join(values):
    return u", ".join(values or [])


def _split(text):
    parts = [p.strip() for p in (text or u"").split(u",")]
    return [p for p in parts if p]


def _elem_name_safe(elem):
    """Local copy of script.py's _elem_name safe-getter — kept independent
    on purpose (see this module's docstring on staying independently
    usable rather than importing from a pushbutton script). Plain .Name
    access raises AttributeError for many *Type classes (WallType,
    FamilySymbol, ...) on this Revit API binding, on both IronPython and
    CPython3 pyRevit engines (pyrevitlabs/pyRevit#854) —
    Element.Name.GetValue(elem) is the confirmed fix, tried first;
    SYMBOL_NAME_PARAM is a second-tier fallback."""
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


class SettingsDialog(object):
    def __init__(self, settings):
        self.settings  = settings
        self.saved     = False
        self._window   = None

    def _build(self):
        ctx    = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        s = self.settings
        w.FindName(u"ArchLinkKw").Text    = _join(s.get(u"ArchLinkKeywords"))
        w.FindName(u"StructLinkKw").Text  = _join(s.get(u"StructLinkKeywords"))
        w.FindName(u"TrafficLinkKw").Text = _join(s.get(u"TrafficLinkKeywords"))
        w.FindName(u"ConcreteKw").Text    = _join(s.get(u"ConcreteKeywords"))
        w.FindName(u"ExcludeKw").Text     = _join(s.get(u"ExcludeKeywords"))
        w.FindName(u"ManualIncludeTypes").Text = _join(s.get(u"ManualIncludeTypeNames"))
        w.FindName(u"ManualExcludeTypes").Text = _join(s.get(u"ManualExcludeTypeNames"))
        w.FindName(u"StructPattern").Text = s.get(u"StructPatternName") or u""
        w.FindName(u"ArchPattern").Text   = s.get(u"ArchPatternName") or u""

        struct_c = s.get(u"StructColor") or {}
        arch_c   = s.get(u"ArchColor") or {}
        w.FindName(u"StructR").Text = unicode(struct_c.get(u"R", 200))
        w.FindName(u"StructG").Text = unicode(struct_c.get(u"G", 30))
        w.FindName(u"StructB").Text = unicode(struct_c.get(u"B", 30))
        w.FindName(u"ArchR").Text   = unicode(arch_c.get(u"R", 0))
        w.FindName(u"ArchG").Text   = unicode(arch_c.get(u"G", 70))
        w.FindName(u"ArchB").Text   = unicode(arch_c.get(u"B", 200))

        w.FindName(u"PathText").Text = u"Saved to: {}".format(cfgmod.settings_file_path())

        self._wire_swatch(u"StructR", u"StructG", u"StructB", u"StructSwatch")
        self._wire_swatch(u"ArchR", u"ArchG", u"ArchB", u"ArchSwatch")
        self._update_swatch(u"StructR", u"StructG", u"StructB", u"StructSwatch")
        self._update_swatch(u"ArchR", u"ArchG", u"ArchB", u"ArchSwatch")

        w.FindName(u"BtnCloseX").Click += lambda s_, e: window.Close()
        w.FindName(u"BtnCancel").Click += lambda s_, e: window.Close()
        w.FindName(u"BtnReset").Click  += self._on_reset
        w.FindName(u"BtnSave").Click   += self._on_save
        w.FindName(u"BtnPickInclude").Click += self._on_pick_include
        w.FindName(u"BtnPickExclude").Click += self._on_pick_exclude

        return window

    def _wire_swatch(self, r_name, g_name, b_name, swatch_name):
        handler = lambda s_, e: self._update_swatch(r_name, g_name, b_name, swatch_name)
        self._window.FindName(r_name).TextChanged += handler
        self._window.FindName(g_name).TextChanged += handler
        self._window.FindName(b_name).TextChanged += handler

    def _update_swatch(self, r_name, g_name, b_name, swatch_name):
        rgb = self._read_rgb(r_name, g_name, b_name)
        if rgb is None:
            return
        w = self._window
        w.FindName(swatch_name).Background = WM.SolidColorBrush(
            WM.Color.FromRgb(rgb[0], rgb[1], rgb[2]))

    def _read_rgb(self, r_name, g_name, b_name):
        w = self._window
        try:
            r = max(0, min(255, int((w.FindName(r_name).Text or u"0").strip())))
            g = max(0, min(255, int((w.FindName(g_name).Text or u"0").strip())))
            b = max(0, min(255, int((w.FindName(b_name).Text or u"0").strip())))
            return (r, g, b)
        except Exception:
            return None

    def _on_reset(self, sender, e):
        defaults = cfgmod._default_copy()
        w = self._window
        w.FindName(u"ArchLinkKw").Text    = _join(defaults[u"ArchLinkKeywords"])
        w.FindName(u"StructLinkKw").Text  = _join(defaults[u"StructLinkKeywords"])
        w.FindName(u"TrafficLinkKw").Text = _join(defaults[u"TrafficLinkKeywords"])
        w.FindName(u"ConcreteKw").Text    = _join(defaults[u"ConcreteKeywords"])
        w.FindName(u"ExcludeKw").Text     = _join(defaults[u"ExcludeKeywords"])
        w.FindName(u"ManualIncludeTypes").Text = _join(defaults[u"ManualIncludeTypeNames"])
        w.FindName(u"ManualExcludeTypes").Text = _join(defaults[u"ManualExcludeTypeNames"])
        w.FindName(u"StructPattern").Text = defaults[u"StructPatternName"]
        w.FindName(u"ArchPattern").Text   = defaults[u"ArchPatternName"]
        sc = defaults[u"StructColor"]
        ac = defaults[u"ArchColor"]
        w.FindName(u"StructR").Text = unicode(sc[u"R"])
        w.FindName(u"StructG").Text = unicode(sc[u"G"])
        w.FindName(u"StructB").Text = unicode(sc[u"B"])
        w.FindName(u"ArchR").Text   = unicode(ac[u"R"])
        w.FindName(u"ArchG").Text   = unicode(ac[u"G"])
        w.FindName(u"ArchB").Text   = unicode(ac[u"B"])
        w.FindName(u"ErrorText").Text = u""

    def _on_save(self, sender, e):
        w = self._window
        err_tb = w.FindName(u"ErrorText")

        struct_rgb = self._read_rgb(u"StructR", u"StructG", u"StructB")
        arch_rgb   = self._read_rgb(u"ArchR", u"ArchG", u"ArchB")
        if struct_rgb is None or arch_rgb is None:
            err_tb.Text = u"Colors must be whole numbers 0-255."
            return

        struct_pattern = (w.FindName(u"StructPattern").Text or u"").strip()
        arch_pattern   = (w.FindName(u"ArchPattern").Text or u"").strip()
        if not struct_pattern or not arch_pattern:
            err_tb.Text = u"Both fill pattern names are required."
            return

        self.settings = {
            u"ArchLinkKeywords"   : _split(w.FindName(u"ArchLinkKw").Text),
            u"StructLinkKeywords" : _split(w.FindName(u"StructLinkKw").Text),
            u"TrafficLinkKeywords": _split(w.FindName(u"TrafficLinkKw").Text),
            u"ConcreteKeywords"   : _split(w.FindName(u"ConcreteKw").Text),
            u"ExcludeKeywords"    : _split(w.FindName(u"ExcludeKw").Text),
            u"ManualIncludeTypeNames": _split(w.FindName(u"ManualIncludeTypes").Text),
            u"ManualExcludeTypeNames": _split(w.FindName(u"ManualExcludeTypes").Text),
            u"StructPatternName"  : struct_pattern,
            u"ArchPatternName"    : arch_pattern,
            u"StructColor"        : {u"R": struct_rgb[0], u"G": struct_rgb[1], u"B": struct_rgb[2]},
            u"ArchColor"          : {u"R": arch_rgb[0], u"G": arch_rgb[1], u"B": arch_rgb[2]},
        }

        try:
            cfgmod.save_settings(self.settings)
        except Exception as ex:
            err_tb.Text = u"Could not save Settings.json: {}".format(ex)
            return

        self.saved = True
        w.Close()

    def _on_pick_include(self, sender, e):
        self._pick_and_add_exception(u"ManualIncludeTypes", u"treated as CONCRETE")

    def _on_pick_exclude(self, sender, e):
        self._pick_and_add_exception(u"ManualExcludeTypes", u"treated as NON-CONCRETE")

    def _pick_and_add_exception(self, field_name, direction_label):
        """"Add from Model..." — pick a rogue linked element directly
        instead of typing its Type Name blind, and append it to whichever
        of the two exception fields the caller points at (`field_name`:
        ManualIncludeTypes or ManualExcludeTypes). Hides this window (not
        Close — Close would end the whole settings session and lose
        unsaved edits in the other fields) for the duration of the pick,
        since a modeless WPF window sitting on screen would otherwise
        cover the Revit view the user needs to click into; PickObject
        itself still runs correctly on this thread either way (this is
        the same Revit API execution context the whole command runs on).
        Re-shows the window afterward regardless of outcome (success,
        cancel, or error) via `finally`."""
        from pyrevit import revit
        w = self._window
        err_tb = w.FindName(u"ErrorText")
        uidoc = revit.uidoc

        w.Hide()
        try:
            try:
                ref = uidoc.Selection.PickObject(
                    ObjectType.LinkedElement,
                    u"Select a wall/column/framing/foundation in a link to be "
                    u"always {} (Esc to cancel)".format(direction_label))
            except RevitExceptions.OperationCanceledException:
                return
            except Exception as ex:
                err_tb.Text = u"Selection failed: {}".format(ex)
                return

            try:
                link_inst = uidoc.Document.GetElement(ref.ElementId)
                link_doc  = link_inst.GetLinkDocument() if link_inst is not None else None
            except Exception:
                link_doc = None
            if link_doc is None:
                err_tb.Text = u"Could not read the picked link's document."
                return

            try:
                elem = link_doc.GetElement(ref.LinkedElementId)
            except Exception:
                elem = None
            if elem is None:
                err_tb.Text = u"Could not resolve the picked element inside its link."
                return

            try:
                elem_type = link_doc.GetElement(elem.GetTypeId())
            except Exception:
                elem_type = None
            type_name = _elem_name_safe(elem_type)
            if not type_name:
                err_tb.Text = u"The picked element has no readable Type Name."
                return

            current = _split(w.FindName(field_name).Text)
            if type_name in current:
                err_tb.Text = u"'{}' is already in that list.".format(type_name)
            else:
                current.append(type_name)
                w.FindName(field_name).Text = _join(current)
                err_tb.Text = u"Added '{}' — click Save to keep it.".format(type_name)
        finally:
            w.Show()

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
        window = self._build()
        frame  = DispatcherFrame()

        def on_closed(s_, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        Dispatcher.PushFrame(frame)
