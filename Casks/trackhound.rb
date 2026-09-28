cask "trackhound" do
  arch arm: "arm64", intel: "x64"

  version "2.1.0"
  sha256 arm:   "0785a923307085b00a74e8ff9f9ef2d4caf6122dbf94c297b279772c0f1e1fca",
         intel: "88e5ae343ba5d0fd6a341192c8cee2b8e939f92a76f893c638adac427e8cb9aa"

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
