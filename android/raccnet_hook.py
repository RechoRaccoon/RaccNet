"""
Buildozer p4a hook — injects network_security_config.xml into the APK so that
Android 9+ allows cleartext HTTP to localhost (needed for the embedded server).

Called by buildozer via:  p4a.hook = raccnet_hook.py
The hook receives a ToolchainCL object (not a buildozer object).
"""
import os
import re
import shutil


def _find_files(root, filename):
    for dirpath, _dirs, files in os.walk(root):
        if filename in files:
            yield os.path.join(dirpath, filename)


def _patch(ctx):
    # The hook runs with cwd = the android/ source directory
    cwd = os.getcwd()
    nsc_src = os.path.join(cwd, 'network_security_config.xml')
    if not os.path.exists(nsc_src):
        print('[RaccNet hook] ERROR: network_security_config.xml not found at', nsc_src)
        return

    # Search for AndroidManifest.xml under .buildozer/
    buildozer_dir = os.path.join(cwd, '.buildozer')
    manifests = list(_find_files(buildozer_dir, 'AndroidManifest.xml'))
    if not manifests:
        print('[RaccNet hook] AndroidManifest.xml not found yet, skipping')
        return

    for manifest_path in manifests:
        # Skip any manifest inside the app assets bundle
        if 'assets' in manifest_path:
            continue

        manifest_dir = os.path.dirname(manifest_path)
        res_xml = os.path.join(manifest_dir, 'res', 'xml')
        os.makedirs(res_xml, exist_ok=True)
        shutil.copy2(nsc_src, os.path.join(res_xml, 'network_security_config.xml'))
        print('[RaccNet hook] Copied network_security_config.xml to', res_xml)

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
            print('[RaccNet hook] Patched', manifest_path)


def before_apk_assemble(ctx, *args, **kwargs):
    _patch(ctx)

def before_build_apk(ctx, *args, **kwargs):
    _patch(ctx)

def after_extract_src(ctx, *args, **kwargs):
    pass
