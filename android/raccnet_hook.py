"""
Buildozer p4a hook — injects network_security_config.xml into the APK so that
Android 9+ allows cleartext HTTP to localhost (needed for the embedded server).

Called by buildozer via:  p4a.hook = raccnet_hook.py
The hook receives a ToolchainCL object (ctx), not a buildozer config object.
"""
import os
import re
import shutil

# The hook lives in the android/ source dir — use __file__ so path is always
# correct regardless of what directory p4a has chdir'd to at call time.
_HOOK_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_files(root, filename):
    for dirpath, _dirs, files in os.walk(root):
        if filename in files:
            yield os.path.join(dirpath, filename)


def _patch(ctx):
    # ── network_security_config.xml sits next to this hook file ───────────────
    nsc_src = os.path.join(_HOOK_DIR, 'network_security_config.xml')
    if not os.path.exists(nsc_src):
        print('[RaccNet hook] ERROR: network_security_config.xml not found at', nsc_src)
        return

    # ── Search for AndroidManifest.xml under .buildozer/ ─────────────────────
    buildozer_dir = os.path.join(_HOOK_DIR, '.buildozer')
    manifests = [
        p for p in _find_files(buildozer_dir, 'AndroidManifest.xml')
        if 'assets' not in p   # skip any copy bundled inside app assets
    ]

    if not manifests:
        print('[RaccNet hook] AndroidManifest.xml not found — skipping patch')
        return

    for manifest_path in manifests:
        manifest_dir = os.path.dirname(manifest_path)

        # Copy network_security_config.xml into res/xml/ beside the manifest
        res_xml = os.path.join(manifest_dir, 'res', 'xml')
        os.makedirs(res_xml, exist_ok=True)
        shutil.copy2(nsc_src, os.path.join(res_xml, 'network_security_config.xml'))
        print('[RaccNet hook] Copied network_security_config.xml →', res_xml)

        with open(manifest_path, 'r', encoding='utf-8') as f:
            content = f.read()

        changed = False
        if 'networkSecurityConfig' not in content:
            content = re.sub(
                r'(<application\b)',
                r'\1 android:networkSecurityConfig="@xml/network_security_config"',
                content, count=1,
            )
            changed = True

        if 'usesCleartextTraffic' not in content:
            content = re.sub(
                r'(<application\b)',
                r'\1 android:usesCleartextTraffic="true"',
                content, count=1,
            )
            changed = True

        if changed:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print('[RaccNet hook] Patched manifest →', manifest_path)
        else:
            print('[RaccNet hook] Manifest already patched, nothing to do')


def before_apk_assemble(ctx, *args, **kwargs):
    _patch(ctx)

def before_build_apk(ctx, *args, **kwargs):
    _patch(ctx)

def after_extract_src(ctx, *args, **kwargs):
    pass
