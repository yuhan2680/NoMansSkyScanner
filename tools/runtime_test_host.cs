// Disposable host for validating Python bootstrap without opening a game.
using System;
using System.IO;
using System.Threading;
using System.Runtime.InteropServices;

internal static class RuntimeTestHost
{
    [DllImport("kernel32.dll")]
    private static extern ulong GetTickCount64();

    private static void Main(string[] args)
    {
        if (args.Length != 1) return;
        DateTime deadline = DateTime.UtcNow.AddMinutes(2);
        ulong initialTicks = GetTickCount64();
        Console.WriteLine("HOST_READY");
        while (!File.Exists(args[0]) && DateTime.UtcNow < deadline)
        {
            if (GetTickCount64() < initialTicks)
            {
                File.WriteAllText(args[0] + ".bad", "Native return value changed");
                return;
            }
            Thread.Sleep(100);
        }
    }
}
