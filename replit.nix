{ pkgs }: {
  deps = [
    pkgs.python310
    pkgs.python310Packages.flask
    pkgs.python310Packages.gunicorn
    # Add other Python packages your app needs
  ];
} 