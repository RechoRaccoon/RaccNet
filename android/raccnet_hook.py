"""
Buildozer p4a hook — injects network_security_config.xml into the APK so that
Android 9+ allows cleartext HTTP to localhost (needed for the embedded server).

Called by buildozer via:  p4a.hook = raccnet_hook.py
"""
import os
import re
import shutil


def _find_files(root, filename):
    """Walk root and yield every path that matches filename."""
    for dirpath, _dirs, files in os.walk(root):
        if filename in files:
            yield os.path.join(dirpath, filename)


def _patch(buildozer):
    # ── Locate the source network_security_config.xml ─────────────────────────
    src_dir = buildozer.config.getdefault('app', 'source.dir', '.')
    nsc_src = os.path.join(src_dir, 'network_security_config.xml')
    if not os.path.exists(nsc_src):
        buildozer.error('RaccNet hook: network_security_config.xml not found in ' + src_dir)
        return

    # ── Find the dist's AndroidManifest.xml (location varies by p4a version) ──
    build_root = os.path.join(buildozer.buildozer_dir, 'android')
    manifests = list(_find_files(build_root, 'AndroidManifest.xml'))
    if not manifests:
        buildozer.info('RaccNet hook: AndroidManifest.xml not found yet, skipping')
        return

    for manifest_path in manifests:
        dist_src = os.path.dirname(manifest_path)

        # ── Copy network_security_config.xml into res/xml/ ────────────────────
        res_xml = os.path.join(dist_src, 'res', 'xml')
        os.makedirs(res_xml, exist_ok=True)
        shutil.copy2(nsc_src, os.path.join(res_xml, 'network_security_config.xml'))
        buildozer.info('RaccNet hook: copied network_security_config.xml to ' + res_xml)

        # ── Patch the manifest ────────────────────────────────────────────────
        with open(manifest_path, 'r', encoding='utf-8') as f:
            content = f.read()

        changed = False
        if 'networkSecurityConfig' not in content:
            content = re.sub(
                r'(<application\b)',
                r'\1 android:networkSecurityConfig="@xml/network_security_config"',
                content,
                count=1,
            )
            changed = True

        # Also ensure usesCleartextTraffic is set as a belt-and-suspenders fallback
        if 'usesCleartextTraffic' not in content:
            content = re.sub(
                r'(<application\b)',
                r'\1 android:usesCleartextTraffic="true"',
                content,
                count=1,
            )
            changed = True

        if changed:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                f.write(content)
            buildozer.info('RaccNet hook: patched ' + manifest_path)


# ── Hook entry points (buildozer calls whichever exists) ──────────────────────
def before_apk_assemble(buildozer, *args, **kwargs):
    _patch(buildozer)

def before_build_apk(buildozer, *args, **kwargs):
    _patch(buildozer)

def after_extract_src(buildozer, *args, **kwargs):
    pass
