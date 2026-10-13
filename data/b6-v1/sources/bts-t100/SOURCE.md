# T-100 Domestic Segment (U.S. Carriers)

- Publisher: U.S. DOT Bureau of Transportation Statistics (BTS), TranStats table FIM
- URL: https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIM
- Licence: Public domain: a work of the U.S. Government, not subject to copyright in the United States (17 U.S.C. § 105).
- Use in b6-v1: JetBlue's passengers / seats by route and month (route-months with at least 10 departures, else the month's): pax = seats x load factor, rounded.

Retrieved: 2026-10-13. Files are under cache/inputs/ (not in git); `python scripts/build_pack.py` fetches what is missing and checks every pin.

| File | Bytes | sha256 | Retrieved | URL |
|---|---|---|---|---|
| bts/t100d_2022_12.csv | 1926869 | `e11b7546d1fd398be387465d396005d4c98215fd894ec7ff6698f625d18f04e6` | 2026-10-13 | https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIM&QO_fu146_anzr=Nv4%20Pn44vr45 |
| bts/t100d_2023_06.csv | 1945134 | `e2014abc485327de0f4e7d9b840aacbacee071783f7f68eaa2084598cabe6b81` | 2026-10-13 | https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIM&QO_fu146_anzr=Nv4%20Pn44vr45 |
| bts/t100d_2024_01.csv | 1902503 | `6b6474f84ff18947870ec2cd72b31d0a494a588abb70d1562d89103c5407d8f8` | 2026-10-13 | https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIM&QO_fu146_anzr=Nv4%20Pn44vr45 |
