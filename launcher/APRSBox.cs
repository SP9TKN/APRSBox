// APRSBox.exe: starts the APRSBox window like APRSBox.cmd, but as a program with the APRSBox icon.
// Uses the project virtual environment (.venv) or the Python bundled with the portable ZIP (python\).
// Build: launcher\build.cmd
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

static class Launcher
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pythonw = null;
        foreach (string candidate in new[] { @".venv\Scripts\pythonw.exe", @"python\pythonw.exe" })
        {
            string path = Path.Combine(root, candidate);
            if (File.Exists(path))
            {
                pythonw = path;
                break;
            }
        }
        if (pythonw == null)
        {
            MessageBox.Show("Brak Pythona: nie ma ani .venv, ani folderu python. Zobacz README-WINDOWS.md.",
                "APRSBox", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var arguments = new StringBuilder(Quote(Path.Combine(root, "APRSBox.pyw")));
        foreach (string arg in args)
        {
            arguments.Append(' ').Append(Quote(arg));
        }
        Process.Start(new ProcessStartInfo(pythonw, arguments.ToString())
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        });
        return 0;
    }

    // One argument for CommandLineToArgvW (how Python reads its command line).
    static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return value;
        }
        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char c in value)
        {
            if (c == '\\')
            {
                backslashes++;
                continue;
            }
            result.Append('\\', c == '"' ? backslashes * 2 + 1 : backslashes).Append(c);
            backslashes = 0;
        }
        result.Append('\\', backslashes * 2).Append('"');
        return result.ToString();
    }
}
