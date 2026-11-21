[app]
title = Ajan
package.name = ajan
package.domain = org.mesut
source.dir = .
source.include_exts = py,html,xml
version = 0.1
requirements = python3,kivy,pyjnius,android,certifi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.extra_manifest_application_arguments = extra_manifest_application_arguments.xml

[buildozer]
log_level = 2
warn_on_root = 1
