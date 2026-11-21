import glob
import os

ATTR = 'android:usesCleartextTraffic="true"'


def _manifests(tc):
    found = []
    try:
        found.append(os.path.join(tc._dist.dist_dir, "src", "main", "AndroidManifest.xml"))
    except Exception:
        pass
    try:
        found += glob.glob(os.path.join(tc.args.storage_dir, "dists", "*", "src", "main", "AndroidManifest.xml"))
    except Exception:
        pass
    return sorted({p for p in found if os.path.exists(p)})


def before_apk_assemble(tc):
    paths = _manifests(tc)
    if not paths:
        print("[hook] AndroidManifest.xml bulunamadi")
    for p in paths:
        s = open(p, encoding="utf-8").read()
        if "usesCleartextTraffic" in s:
            print("[hook] cleartext zaten var:", p)
            continue
        s = s.replace("<application", "<application " + ATTR, 1)
        open(p, "w", encoding="utf-8").write(s)
        print("[hook] cleartext eklendi:", p)
