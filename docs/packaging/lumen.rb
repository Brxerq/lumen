# Homebrew formula for a tap (homebrew-lumen). Bump version and the sha256
# lines from the release's SHA256SUMS; everything else stays.
class Lumen < Formula
  desc "Something happens on your computer, the devices around you react"
  homepage "https://github.com/Brxerq/lumen"
  version "0.5.0"
  license "MIT"

  on_macos do
    url "https://github.com/Brxerq/lumen/releases/download/v#{version}/lumen-macos"
    sha256 "REPLACE_WITH_SHA256_OF_lumen-macos"
  end

  on_linux do
    url "https://github.com/Brxerq/lumen/releases/download/v#{version}/lumen-linux"
    sha256 "REPLACE_WITH_SHA256_OF_lumen-linux"
  end

  def install
    binary = OS.mac? ? "lumen-macos" : "lumen-linux"
    bin.install binary => "lumen"
  end

  service do
    run [opt_bin/"lumen"]
    keep_alive true
    log_path var/"log/lumen.log"
    error_log_path var/"log/lumen.log"
  end

  test do
    assert_match "selfcheck: ok", shell_output("#{bin}/lumen selfcheck")
  end
end
