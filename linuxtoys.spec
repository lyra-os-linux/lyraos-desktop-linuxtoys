Name:           linuxtoys
Version:        7.2.4
Release:        0
%global debug_package %{nil}
Summary:        Graphical collection of tools for Linux
License:        GPL-3.0-only
URL:            https://linux.toys/
Source0:        linuxtoys-%{version}.tar.xz
Source1:        linuxtoys-update-self
Patch0:         linuxtoys-disable-self-update.patch
BuildRequires:  desktop-file-utils
BuildRequires:  hicolor-icon-theme
BuildRequires:  python3
Requires:       AppStream
Requires:       typelib-1_0-AppStream-1.0
Requires:       bash
Requires:       curl
Requires:       git
Requires:       gtk3
Requires:       hicolor-icon-theme
Requires:       libvte-2_91-0
Requires:       python3
Requires:       python3-gobject
Requires:       python3-requests
Requires:       python3dist(urllib3)
Requires:       python3-certifi
Requires:       sudo
Requires:       typelib-1_0-Vte-2.91
Requires:       wget
Requires:       xdg-utils
Requires:       zenity
ExclusiveArch:  x86_64

%description
LinuxToys presents a curated collection of Linux tools and configuration
helpers through a graphical interface. This package disables LinuxToys'
upstream self-installer so application updates remain managed by RPM and the
signed Lyra repositories.

%prep
%autosetup -p1

%build
# The upstream release tarball contains an already assembled, interpreted
# application tree. There is no compilation step.

%install
mkdir -p %{buildroot}%{_prefix}
cp -a usr/. %{buildroot}%{_prefix}/
install -D -m 0755 %{SOURCE1} \
    %{buildroot}%{_datadir}/linuxtoys/helpers/update_self.sh
# Normalize executable interpreters so RPM detects the real runtime dependency.
find %{buildroot}%{_datadir}/linuxtoys -type f -name '*.sh' \
    -exec sed -i '1s|^#!/usr/bin/env bash$|#!/bin/bash|' {} +
sed -i '1s|^#!/usr/bin/env python3$|#!/usr/bin/python3|' \
    %{buildroot}%{_datadir}/linuxtoys/linuxtoys.py
find %{buildroot} -type d -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot} -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
desktop-file-validate %{buildroot}%{_datadir}/applications/LinuxToys.desktop

%check
if grep -R -E 'https://linux\.toys/install\.sh|git[[:space:]]+pull' \
    %{buildroot}%{_bindir}/linuxtoys %{buildroot}%{_datadir}/linuxtoys; then
    echo 'upstream self-update bypasses RPM ownership' >&2
    exit 1
fi
test -x %{buildroot}%{_bindir}/linuxtoys
test -f %{buildroot}%{_datadir}/applications/LinuxToys.desktop
grep -qxF '__version__ = "%{version}"' \
    %{buildroot}%{_datadir}/linuxtoys/app/updater/__init__.py
bash -n %{buildroot}%{_bindir}/linuxtoys
bash -n %{buildroot}%{_datadir}/linuxtoys/helpers/update_self.sh
python3 -m compileall -q usr/share/linuxtoys

%files
%license %{_datadir}/linuxtoys/LICENSE
%{_bindir}/linuxtoys
%{_datadir}/applications/LinuxToys.desktop
%{_datadir}/icons/hicolor/*/apps/*
%exclude %{_datadir}/linuxtoys/LICENSE
%{_datadir}/linuxtoys/

%changelog
