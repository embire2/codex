"""Synthetic ConPTY handle probe only; not native branding/release acceptance."""
from pathlib import Path
import json, os, subprocess, tempfile
from windows_conpty_probe_helper import capture_idle

SOURCE = r'''
using System;
using System.Runtime.InteropServices;
public class Probe {
 [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr GetStdHandle(int kind);
 [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetConsoleMode(IntPtr handle, out uint mode);
 [DllImport("kernel32.dll")] static extern uint GetFileType(IntPtr handle);
 public static int Main(string[] args) {
   foreach (int kind in new int[] {-10,-11,-12}) {
     IntPtr handle=GetStdHandle(kind); uint mode; bool ok=GetConsoleMode(handle,out mode);
     Console.WriteLine("HANDLE kind="+kind+" value="+handle+" console="+ok+" mode="+mode+" type="+GetFileType(handle)+" lastError="+Marshal.GetLastWin32Error());
   }
   Console.WriteLine("REDIRECTED stdin="+Console.IsInputRedirected+" stdout="+Console.IsOutputRedirected+" stderr="+Console.IsErrorRedirected);
   Console.WriteLine("SYNTHETIC TERMINAL FIXTURE: Ask CoderX | MagnetAPI.org Codex CLI");
   return Console.IsInputRedirected ? 2 : 0;
 }
}
'''

with tempfile.TemporaryDirectory(prefix="conpty-handle-probe-") as directory:
 home=Path(directory);source=home/'probe.cs';binary=home/'probe.exe';source.write_text(SOURCE)
 compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
 subprocess.run([str(compiler),'/nologo','/target:exe','/out:'+str(binary),str(source)],check=True,capture_output=True,text=True,timeout=60)
 env={**os.environ,'HOME':directory,'USERPROFILE':directory,'TERM':'xterm-256color'}
 text=capture_idle(binary,home,env,arguments=[])
 record={'qualification':'synthetic terminal attachment only; not native CLI branding','captured':text,'stdinConsole': 'kind=-10' in text and 'console=True' in text,'allStdioAttached': all(('kind='+str(k)) in text for k in [-10,-11,-12]) and 'REDIRECTED stdin=False stdout=False stderr=False' in text}
 out=Path(os.environ['GITHUB_WORKSPACE'])/'terminal-probe.json';out.write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
 assert record['allStdioAttached'], 'Our ConPTY harness must actually attach all three console handles'
