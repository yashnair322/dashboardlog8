{pkgs}: {
  deps = [
    pkgs.postgresql
    pkgs.rustc
    pkgs.libiconv
    pkgs.cargo
  ];
}
