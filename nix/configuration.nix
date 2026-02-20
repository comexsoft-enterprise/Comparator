{
  pkgs,
  inputs,
  ...
}:
{
  imports = [
    (inputs.nixpkgs + "/nixos/modules/virtualisation/amazon-image.nix")
    ./firewall.nix
    # ./comparator.nix
    ./docker-compose.nix
  ];

  ec2.hvm = true;
  networking.hostName = "ec2comparator";

  environment.systemPackages = with pkgs; [
    mosh
    vim
    git
    jujutsu
    btop
  ];

  nix.settings.trusted-users = [ "@wheel" ];
  security.sudo.wheelNeedsPassword = false;

  services.openssh.settings.PasswordAuthentication = false;

  # Disable reinitialisation of AMI on restart or power cycle
  systemd.services.amazon-init.enable = false;

  users.users.monk3yd = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIPic7rY0bf2WDhRclFOV61Xv5BwjhGXrMpc1vnkEC5w jegajardog@gmail.com"
    ];
  };

  nix.gc = {
    automatic = true;
    dates = "weekly UTC";
  };

  swapDevices = [
    {
      device = "/swapfile";
      priority = 10;
      size = 1024;
    }
  ];

  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];

  system.stateVersion = "25.05";
}
