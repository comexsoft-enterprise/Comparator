{ ... }:
{
  # Placeholder for comparator runtime.
  # Next step: add systemd unit to run uvicorn, and/or import ./comparator/docker-compose.nix.

  systemd.tmpfiles.rules = [
    # Runtime env file (do not store secrets in the Nix store)
    "f /etc/comparator.env 0600 root root - -"
  ];
}
