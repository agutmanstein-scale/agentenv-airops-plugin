# The viewer's sound: sources

Every recording here is from [Freesound](https://freesound.org) under **CC0 1.0** (public domain dedication,
<https://creativecommons.org/publicdomain/zero/1.0/>), each licence checked on its page. No ATC, LiveATC or
announcement audio is used. The files are cut from Freesound's high-quality previews, because downloading an original
needs an account; Pixabay refuses automated access (HTTP 403), so nothing here comes from it.

Each loop is cut, filtered (a high-pass, and for some a dynamic normaliser or soft limiter), made to loop by crossfading
its tail into its head, levelled to -23 LUFS (one-shots to a peak) and encoded as Opus in Ogg, 48 kHz.

| File | Plays | Source | Author | Cut (s) |
|---|---|---|---|---|
| `apron.ogg` | the airport's bed outdoors | [Runway Ambience](https://freesound.org/people/clif_creates/sounds/251977/) (LAX, jets on the runway) | clif_creates | 0.3–26.4, mono |
| `idle.ogg` | engines at idle: taxi, hold, parked, engine start | [Runway of airport with airplane](https://freesound.org/people/BonnyOrbit/sounds/465440/) | BonnyOrbit | 0.5–35.6 |
| `roar.ogg` | the takeoff roar, near | [AEROJet_Plane Takeoff_OLS_SFX_Gravelly Point](https://freesound.org/people/orange.line.sound/sounds/872922/) (DCA) | orange.line.sound | 2.6–12.4 |
| `roar-far.ogg` | the takeoff roar, far | [Takeoff 5 (Distant)](https://freesound.org/people/clif_creates/sounds/251976/) (LAX) | clif_creates | 1.6–21.6, mono |
| `approach.ogg` | an airliner on approach | [Boeing777-236ER](https://freesound.org/people/freezound5/sounds/788872/) | freezound5 | 9.0–31.0, normalised |
| `reverse.ogg` | reverse thrust on the rollout | [Boeing777-236ER](https://freesound.org/people/freezound5/sounds/788872/) (after touchdown) | freezound5 | 51.0–69.0, normalised |
| `tug.ogg` | the pushback tug | [Diesel idle 1](https://freesound.org/people/C-V/sounds/565598/) | C-V | 1.0–41.0, mono |
| `terminal.ogg` | the terminal's hall | [SeaTac Airport Terminal Ambience](https://freesound.org/people/loganzsound/sounds/868379/) | loganzsound | 20.0–110.0 |
| `crowd.ogg` | a gate area's crowd, by its people | [Crowd walla](https://freesound.org/people/knufds/sounds/504906/) | knufds | 10.0–70.0 |
| `keys.ogg` | a desk's keyboard | [Typing on a keyboard](https://freesound.org/people/SamsterBirdies/sounds/489424/) | SamsterBirdies | 2.0–42.0, mono |
| `click.ogg` | a mouse click | [Mouse Click Sound](https://freesound.org/people/Pixeliota/sounds/678248/) | Pixeliota | 0–0.5, mono |
| `rain.ogg` | rain | [AMB_M_City_Rain_Heavy](https://freesound.org/people/conleec/sounds/171981/) | conleec | 5.0–50.0 |
| `wind.ogg` | wind | [Strong Wind Recorded with Soundfield Microphone Manchester 3](https://freesound.org/people/keirofinch/sounds/376533/) | keirofinch | 0.5–58.5 |
| `thunder.ogg` | thunder near the field | [Thunder Clap OWB KY](https://freesound.org/people/Dave%20Welsh/sounds/194364/) | Dave Welsh | 11.5–41.5 |
| `rumble.ogg` | distant thunder, four rumbles of 9.5 s | [Thunder DRY rolling distant low rumbles](https://freesound.org/people/TRP/sounds/717845/) | TRP | 1.5–11, 46–55.5, 76.5–86, 147–156.5 |

Synthesized in `audio.js` (no recording): the APU's whine, the control-center room's tone and air conditioning, the
terminal's air, the chime (two bell tones through a synthetic hall), and a stand-in for any file that fails to load.
