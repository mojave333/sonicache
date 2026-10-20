cask "sonicache" do
  arch arm: "arm64", intel: "x64"

  version "1.0.0"
  sha256 arm:   "73e9ff6965afe13afdde953208d9b8cf57f518a5029473a3bd7375d84d90914f",
         intel: "8c00734ac7a7b9ddc4f5da226a0996a0f4198d2d38324a378afec3adae8a5ade"

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
