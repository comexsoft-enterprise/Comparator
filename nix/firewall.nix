{ ... }:
{
  services.openssh.listenAddresses = [
    {
      addr = "0.0.0.0";
      port = 22;
    }
  ];

  networking.firewall.allowedTCPPorts = [
    22
    80
    443
    8000
  ];

  networking.firewall.allowedUDPPortRanges = [
    {
      from = 60000;
      to = 60010;
    }
  ];
}
