# Homebrew cask template. It goes in a tap repository named
# "<owner>/homebrew-tap" (Casks/garmin-export.rb), then:
#     brew install --cask <owner>/tap/garmin-export
# Update version and both sha256 at each release:
#     shasum -a 256 garmin-export-vX.Y.Z-macos-*.zip
cask "garmin-export" do
  arch arm: "arm64", intel: "x86_64"

  version "0.2.0"
  sha256 arm:   "REPLACE_WITH_SHA256_OF_macos-arm64.zip",
         intel: "REPLACE_WITH_SHA256_OF_macos-x86_64.zip"

  url "https://github.com/marcbnf/garmin-export/releases/download/v#{version}/garmin-export-v#{version}-macos-#{arch}.zip"
  name "Garmin Export"
  desc "Export Garmin Connect health and fitness data to LLM-ready plain text"
  homepage "https://github.com/marcbnf/garmin-export"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: ">= :big_sur"

  app "Garmin Export.app"

  # Not notarized yet: remove the quarantine flag so Gatekeeper lets it open.
  # Delete this block once releases are signed with a Developer ID.
  postflight do
    system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{appdir}/Garmin Export.app"]
  end

  zap trash: [
    "~/Library/Application Support/GarminExport",
  ]
end
