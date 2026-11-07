param([long]$WindowHandle, [switch]$IncludeOffscreen)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName Accessibility
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Accessibility;
public class CheckinAccNode {
 public string name, kind, id, value;
 public int x,y,width,height,state;
 public bool enabled;
}
public class CheckinAccessibility {
 [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr h,uint id,ref Guid iid,[MarshalAs(UnmanagedType.Interface)] out IAccessible a);
 [DllImport("oleacc.dll")] static extern int AccessibleChildren(IAccessible a,int start,int count,[Out,MarshalAs(UnmanagedType.LPArray,SizeParamIndex=2)] object[] children,out int obtained);
 public static List<CheckinAccNode> Read(long h) {
  var list=new List<CheckinAccNode>();
  Guid iid=new Guid("618736e0-3c3d-11cf-810c-00aa00389b71");
  IAccessible a;
  if(AccessibleObjectFromWindow(new IntPtr(h),unchecked((uint)-4),ref iid,out a)>=0) Walk(a,0,list,0);
  return list;
 }
 static void Walk(IAccessible a,object id,List<CheckinAccNode> list,int depth) {
  if(a==null || depth>40 || list.Count>=1500)return;
  try {
   int state=Convert.ToInt32(a.get_accState(id));
   int x,y,w,h; a.accLocation(out x,out y,out w,out h,id);
   int role=Convert.ToInt32(a.get_accRole(id));
   string value=""; try { value=a.get_accValue(id) ?? ""; }catch{}
   string name=a.get_accName(id) ?? "";
   string kind=role==41?"Text":role==42?"Edit":role==30?"Hyperlink":role==34?"ListItem":role==40?"Image":role==46?"ComboBox":role==43?"Button":(!String.IsNullOrEmpty(name)?"Text":"Pane");
   if(w>0 && h>0 && (state & 0x18000)==0)list.Add(new CheckinAccNode{name=name,value=value,state=state,kind=kind,id="msaa",x=x,y=y,width=w,height=h,enabled=(state & 1)==0});
  }catch{}
  if(!(id is int) || (int)id!=0)return;
  try {
   int count=Math.Min(a.accChildCount,1500);
   object[] kids=new object[count];int got;
   AccessibleChildren(a,0,count,kids,out got);
   for(int i=0;i<got;i++) {
    var child=kids[i] as IAccessible;
    if(child!=null)Walk(child,0,list,depth+1);
    else if(kids[i] is int)Walk(a,kids[i],list,depth+1);
   }
  }catch{}
 }
}
"@ -ReferencedAssemblies Accessibility
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if ($null -eq $root) { throw 'Target window disappeared' }
$collection = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$items = @()
foreach ($element in $collection) {
    try {
        $current = $element.Current
        $rectangle = $current.BoundingRectangle
        if ($rectangle.IsEmpty -or ($current.IsOffscreen -and !$IncludeOffscreen)) { continue }
        if ($rectangle.Width -le 0 -or $rectangle.Height -le 0) { continue }
        $controlValue = ''
        try {
            # Availability is an AutomationElement property, not a member of
            # AutomationElementInformation ($element.Current). Unsupported
            # controls throw here and are handled by the surrounding catch.
            $valuePattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            $controlValue = [string]$valuePattern.Current.Value
        } catch { }
        $items += [pscustomobject]@{
            name = $current.Name; value = $controlValue
            kind = if ($current.ClassName -eq 'Edit') { 'Edit' } else { $current.ControlType.ProgrammaticName.Replace('ControlType.','') }
            id = $current.AutomationId; handle = $current.NativeWindowHandle
            x = $rectangle.X; y = $rectangle.Y
            width = $rectangle.Width; height = $rectangle.Height
            enabled = $current.IsEnabled; offscreen = $current.IsOffscreen
        }
        if ($items.Count -ge 1500) { break }
    } catch { continue }
}
# Chromium exposes webview content through MSAA.
foreach ($pane in @($items | Where-Object { $_.name -eq 'Chrome Legacy Window' -and $_.handle })) {
    foreach ($node in [CheckinAccessibility]::Read([long]$pane.handle)) {
        $items += [pscustomobject]@{ name=$node.name; value=$node.value; state=$node.state; kind=$node.kind; id=$node.id; x=$node.x; y=$node.y; width=$node.width; height=$node.height; enabled=$node.enabled }
    }
}
$jsonText = ConvertTo-Json -InputObject @($items) -Depth 3 -Compress
[regex]::Replace($jsonText, '[^\x00-\x7F]', [System.Text.RegularExpressions.MatchEvaluator]{ param($match) '\u' + ([int][char]$match.Value).ToString('x4') })

