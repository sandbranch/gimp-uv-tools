# The dialogs of UV Tools and their undo, in a real GIMP (the Flatpak GUI
# on a Broadway display, seen through a headless Chrome with
# gimp-devtools/gui/cdp.mjs).
#
# For each tool: its dialog opens from the procedure (a screenshot is
# kept in tests/output/gui/), OK runs it, and one Edit > Undo takes back
# all of it (the image is as before: layers, paths, channels, pixels).
#
#   UVT_GUI_LOOK=1 python3 tests/gui/gui_test.py   screenshots only
#
# Started by tests/gui/gui-test.sh. GIMP runs with --new-instance (else
# it would hand its batch command to a GIMP that is already running),
# isolated from your folders by gimp-devtools/gimp-run.sh (a
# throwaway home and profile). Stops the GIMP and Chrome it started, and
# nothing else.
#
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
tests = os.path.dirname(here)
src = os.path.dirname(tests)
out = os.environ["UVT_GUI_OUT"]
cases = os.environ["UVT_CASES"]
profile = os.environ["UVT_GUI_PROFILE"]
devtools = os.environ.get("GIMP_PLUGIN_DEVTOOLS", os.path.join(src, "..", "gimp-devtools"))
look = os.environ.get("UVT_GUI_LOOK") == "1"
cdp_js = os.path.join(devtools, "gui", "cdp.mjs")
failures = 0


def check(name, ok, detail=""):
    global failures
    print("UVT %s gui %s%s" % ("PASS" if ok else "FAIL", name,
                               (": %s" % (detail,)) if not ok and detail != "" else ""), flush=True)
    if not ok:
        failures += 1
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


broadway = free_port()
display = ":%d" % (20 + broadway % 70)
env = dict(os.environ, CDP_PORT=str(free_port()))
view = "size:1400,900"


def cdp(*actions, timeout=90):
    r = subprocess.run(["node", cdp_js, view] + list(actions), env=env, capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def shot(name):
    cdp("wait:600", "shot:%s" % os.path.join(out, name))


def our_gimp():
    r = subprocess.run(["flatpak", "ps", "--columns=instance,child-pid,application"],
                       capture_output=True, text=True)
    found = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[2] != "org.gimp.GIMP":
            continue
        try:
            cmd = open("/proc/%s/cmdline" % parts[1], "rb").read().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if profile in cmd or ("--port %d " % broadway) in cmd:
            found.append(parts[0])
    return found


N = [0]


def send(cmd, wait=True, timeout=120):
    N[0] += 1
    n = N[0]
    p = os.path.join(out, "cmd-%d.json" % n)
    with open(p + ".tmp", "w") as f:
        json.dump(cmd, f)
    os.rename(p + ".tmp", p)
    return result(n, timeout) if wait else n


def result(n, timeout=120):
    p = os.path.join(out, "res-%d.json" % n)
    end = time.time() + timeout
    while time.time() < end and not os.path.exists(p):
        time.sleep(0.2)
    if not os.path.exists(p):
        return None
    return json.load(open(p))


MOVE = ("eval:(() => { for (const s of Object.values(surfaces)) { if (s.x < 0 || s.y < 0) {"
        " cmdMoveResizeSurface(s.id, true, Math.max(0, s.x), Math.max(0, s.y), false, 0, 0);"
        " sendConfigureNotify(s); } } return Object.keys(surfaces).length })()")
TOP = ("eval:(() => { const s = Object.values(surfaces).filter(s => s.width > 200 &&"
       " s.visible !== false).sort((a, b) => (Number(b.canvas.style.zIndex) || 0) -"
       " (Number(a.canvas.style.zIndex) || 0))[0]; return s ? [s.x, s.y, s.width, s.height]"
       ".join(',') : '' })()")
COUNT = "eval:Object.values(surfaces).filter(s => s.visible !== false && s.width > 200).length"


def surfaces_count():
    rc, t = cdp(COUNT)
    v = t.splitlines()[-1] if t else ""
    return int(v) if v.isdigit() else -1


def top_window():
    rc, t = cdp(MOVE, "wait:300", TOP)
    v = t.splitlines()[-1] if t else ""
    try:
        return [int(float(a)) for a in v.split(",")]
    except ValueError:
        return None


# where things are (Broadway page coordinates), from the screenshots:
# GIMP's single window at 0, 0 (tests/gui/gui-test.sh writes the
# sessionrc); dialog buttons from the dialog's bottom right corner,
# which includes Broadway's window shadow
EDIT_MENU = (84, 72)
OK_FROM_BR = (-94, -71)


def dialog_ok(name, before):
    """Waits for the dialog, keeps a screenshot, clicks OK."""
    end = time.time() + 60
    while time.time() < end and surfaces_count() <= before:
        time.sleep(0.5)
    time.sleep(1.5)
    win = top_window()
    shot("%s-dialog.png" % name)
    print("UVT INFO gui %s: dialog window at %s" % (name, win), flush=True)
    if not check("%s: the dialog opens" % name, win is not None and surfaces_count() > before, win):
        return False
    if look:
        return True
    x, y, w, h = win
    cdp("click:%d,%d" % (x + w + OK_FROM_BR[0], y + h + OK_FROM_BR[1]), "wait:500")
    return True


def undo(name):
    """Edit > Undo, from the menu (the first item), by mouse."""
    cdp("click:%d,%d" % EDIT_MENU, "wait:1000")
    shot("%s-undo-menu.png" % name)
    cdp("key:Down", "wait:300", "key:Enter", "wait:1500")


chrome = gimp = None
chrome_bin = shutil.which("google-chrome") or shutil.which("chromium") or \
    shutil.which("chromium-browser")
try:
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.startswith(("cmd-", "res-")):
            os.unlink(os.path.join(out, f))
    chrome = subprocess.Popen([chrome_bin, "--headless=new",
                               "--remote-debugging-port=%s" % env["CDP_PORT"],
                               "--user-data-dir=%s" % os.path.join(out, "chrome"),
                               "--password-store=basic", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = open(os.path.join(out, "gimp.log"), "w")
    script = os.path.join(here, "gui_script.py")
    gimp = subprocess.Popen([
        os.path.join(devtools, "gimp-run.sh"), "--flatpak", "--home=%s" % os.path.join(out, "home"),
        "--filesystem=%s" % src, "--env=GDK_BACKEND=broadway", "--env=BROADWAY_DISPLAY=%s" % display,
        "--env=GIMP3_DIRECTORY=%s" % profile, "--env=UVT_GUI_OUT=%s" % out,
        "--env=UVT_SRC=%s" % src, "--env=UVT_CASES=%s" % cases, "--", "sh", "-c",
        "broadwayd --port %d %s & bw=$!; trap 'kill $bw' EXIT; sleep 4; "
        "gimp-3.2 --new-instance --no-splash --no-fonts --batch-interpreter python-fu-eval "
        "-b \"exec(open('%s').read())\"" % (broadway, display, script)],
        stdout=log, stderr=subprocess.STDOUT)
    time.sleep(2.5)
    rc, text = cdp("nav:http://127.0.0.1:%d/" % broadway, "wait:500")
    check("Chrome shows the Broadway page", rc == 0, text)
    r = result(0, timeout=240)
    check("GIMP started with the test script", r is not None and r.get("ready"))
    r = send({"do": "open"})
    cdp(MOVE, "wait:500", "key:Escape", "wait:500")
    shot("0-image.png")
    base = r["state"]
    check("an image in a window", r["ok"] and base["layers"] == ["Texture"], r)

    # 1. Import UV Layout
    before = surfaces_count()
    n = send({"do": "import", "case": "monkey", "dialog": True}, wait=False)
    dialog_ok("1-import", before)
    r = result(n, 180)
    st = r and r["state"]
    shot("1-import-done.png")
    if not look:
        check("import: OK runs it (139 island paths, a channel, the outline shown)",
              st is not None and st["status"] == "success" and st["island_paths"] == 139
              and "UV Islands" in st["channels"], st)
        undo("1-import")
        s2 = send({"do": "state"})["state"]
        check("import: one Undo takes it all back",
              s2["paths"] == 0 and s2["channels"] == [] and s2["pixels"] == base["pixels"], s2)
        shot("1-import-undone.png")
        check("import: the undo shows no message (a message window would open)",
              surfaces_count() == 1, surfaces_count())
        send({"do": "import", "case": "monkey"})

        # 2. Select UV Islands (touching a small rectangle)
        send({"do": "rect-in-island", "case": "monkey"})
        s1 = send({"do": "state"})["state"]
        before = surfaces_count()
        n = send({"do": "run", "proc": "plug-in-uv-tools-select-islands"}, wait=False)
        dialog_ok("2-select", before)
        st = result(n)["state"]
        shot("2-select-done.png")
        check("select islands: the selection grows to the island", st["status"] == "success"
              and st["selection"] != s1["selection"], (s1["selection"], st["selection"]))
        undo("2-select")
        s2 = send({"do": "state"})["state"]
        check("select islands: one Undo gives the small selection back",
              s2["selection"] == s1["selection"], s2["selection"])
        send({"do": "rect", "rect": [0, 0, 1024, 1024]})
        send({"do": "rect", "rect": [0, 0, 0, 0]})

        # 3. Seam Bleed on the texture
        send({"do": "select-layer", "name": "Texture"})
        s1 = send({"do": "state"})["state"]
        before = surfaces_count()
        n = send({"do": "run", "proc": "plug-in-uv-tools-seam-bleed"}, wait=False)
        dialog_ok("3-bleed", before)
        st = result(n, 180)["state"]
        shot("3-bleed-done.png")
        check("seam bleed: the pixels change, the layer keeps its name and tattoo",
              st["status"] == "success" and st["pixels"] != s1["pixels"]
              and st["tattoos"] == s1["tattoos"] and st["layers"] == s1["layers"], st)
        undo("3-bleed")
        s2 = send({"do": "state"})["state"]
        check("seam bleed: one Undo gives the pixels back", s2["pixels"] == s1["pixels"]
              and s2["tattoos"] == s1["tattoos"], s2)

        # 4. Fill Outside Islands
        s1 = send({"do": "state"})["state"]
        before = surfaces_count()
        n = send({"do": "run", "proc": "plug-in-uv-tools-fill-outside"}, wait=False)
        dialog_ok("4-fill", before)
        st = result(n, 120)["state"]
        shot("4-fill-done.png")
        check("fill outside: pixels change", st["status"] == "success"
              and st["pixels"] != s1["pixels"], st["status"])
        undo("4-fill")
        s2 = send({"do": "state"})["state"]
        check("fill outside: one Undo gives them back", s2["pixels"] == s1["pixels"]
              and s2["selection"] == s1["selection"], s2)

        # 5. Pack Channels (the three selected layers)
        r = send({"do": "grey-layers"})
        check("three grey layers to pack", r["ok"] and len(r["state"]["layers"]) == 4, r)
        s1 = send({"do": "state"})["state"]
        before = surfaces_count()
        n = send({"do": "run", "proc": "plug-in-uv-tools-pack-channels"}, wait=False)
        dialog_ok("5-pack", before)
        st = result(n, 120)["state"]
        shot("5-pack-done.png")
        check("pack channels: a new layer Packed", st["status"] == "success"
              and "Packed" in st["layers"], st["layers"])
        undo("5-pack")
        s2 = send({"do": "state"})["state"]
        check("pack channels: one Undo removes it", s2["layers"] == s1["layers"]
              and s2["pixels"] == s1["pixels"], s2["layers"])

        # 6. Unpack Channels (no dialog)
        send({"do": "select-layer", "name": "Texture"})
        s1 = send({"do": "state"})["state"]
        st = send({"do": "run", "proc": "plug-in-uv-tools-unpack-channels"})["state"]
        check("unpack channels: four grey layers", st["status"] == "success"
              and len(st["layers"]) == len(s1["layers"]) + 4, st["layers"])
        undo("6-unpack")
        s2 = send({"do": "state"})["state"]
        check("unpack channels: one Undo removes them", s2["layers"] == s1["layers"], s2["layers"])
    else:
        for proc, name in [("plug-in-uv-tools-select-islands", "2-select"),
                           ("plug-in-uv-tools-seam-bleed", "3-bleed"),
                           ("plug-in-uv-tools-fill-outside", "4-fill"),
                           ("plug-in-uv-tools-pack-channels", "5-pack")]:
            before = surfaces_count()
            n = send({"do": "run", "proc": proc}, wait=False)
            dialog_ok(name, before)
            win = top_window()
            if win:
                x, y, w, h = win
                cdp("click:%d,%d" % (x + w - 150, y + h - 44), "wait:800")
            result(n, 60)
        cdp("click:%d,%d" % EDIT_MENU, "wait:1000")
        shot("look-edit-menu.png")
        cdp("key:Escape")
    send({"do": "quit"}, wait=False)
    time.sleep(3)
except Exception as e:
    import traceback
    traceback.print_exc()
    check("GUI test ran", False, e)
finally:
    if chrome is not None:
        chrome.send_signal(signal.SIGTERM)
        try:
            chrome.wait(10)
        except subprocess.TimeoutExpired:
            chrome.kill()
    for inst in our_gimp():
        subprocess.run(["flatpak", "kill", inst])
    if gimp is not None:
        try:
            gimp.wait(15)
        except subprocess.TimeoutExpired:
            gimp.kill()
    time.sleep(1)
    check("our GIMP and Chrome stopped", not our_gimp() and (chrome is None or chrome.poll()
                                                              is not None))
print("UVT GUI failures: %d" % failures, flush=True)
sys.exit(1 if failures else 0)
