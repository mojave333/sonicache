cask "sonicache" do
  arch arm: "arm64", intel: "x64"

  version "1.0.0"
  sha256 arm:   "2a645cec2974d7e7a96b795870eddad39cb6a748cd27b4d1023a0f48032e322d",
         intel: "c3b465c9c1cc6268a88ac93a448ad0649aba4ed9a51aaf2fcf8b412d25ab6b10"

  url "https://github.com/mojave333/sonicache/releases/download/v#{version}/Sonicache-v#{version}-macos-#{arch}.dmg"
  name "Sonicache"
  desc "Music downloader, library and player"
  homepage "https://github.com/mojave333/sonicache"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "Sonicache.app"
  binary "#{appdir}/Sonicache.app/Contents/MacOS/Sonicache-cli", target: "sonicache"

  zap trash: [
    "~/.sonicache.json",
    "~/Library/Application Support/Sonicache",
  ]

  caveats <<~EOS
    Sonicache is not signed by Apple, so macOS refuses to open it the first time.
    Either right-click Sonicache in Applications and choose Open, or run:
      xattr -dr com.apple.quarantine /Applications/Sonicache.app
  EOS
end
