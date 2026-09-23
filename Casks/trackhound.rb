cask "trackhound" do
  arch arm: "arm64", intel: "x64"

  version "2.0.0"
  sha256 arm:   "a758f3920793579844e9e1817357d41bdb35f59cd35d1d3c85acd39734ed5c1b",
         intel: "53b2a0c79e77216691acfcb9575b07481d19584c6cd3042c9024f411b7e6d235"

  url "https://github.com/mojave333/trackhound/releases/download/v#{version}/Trackhound-v#{version}-macos-#{arch}.dmg"
  name "Trackhound"
  desc "Music downloader, library and player"
  homepage "https://github.com/mojave333/trackhound"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "Trackhound.app"
  binary "#{appdir}/Trackhound.app/Contents/MacOS/Trackhound-cli", target: "trackhound"

  zap trash: [
    "~/.trackhound.json",
    "~/Library/Application Support/Trackhound",
  ]

  caveats <<~EOS
    Trackhound is not signed by Apple, so macOS refuses to open it the first time.
    Either right-click Trackhound in Applications and choose Open, or run:
      xattr -dr com.apple.quarantine /Applications/Trackhound.app
  EOS
end
