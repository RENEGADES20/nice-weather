"""Capture NOAA HRRR 2m-temperature forecasts at the KNYC nearest grid point."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from nice_weather.trading.feed import FeedStore


def point(body, cycle, hour):
    import eccodes

    handle = eccodes.codes_new_from_message(body)
    try:
        if (
            eccodes.codes_get(handle, "shortName") != "2t"
            or eccodes.codes_get(handle, "forecastTime") != hour
            or eccodes.codes_get(handle, "dataDate") != int(cycle.strftime("%Y%m%d"))
            or eccodes.codes_get(handle, "dataTime") != cycle.hour * 100
        ):
            raise ValueError("HRRR field/cycle mismatch")
        nearest = eccodes.codes_grib_find_nearest(handle, 40.7789, -73.9692)[0]
        kelvin = float(nearest["value"])
        if not 180 < kelvin < 350 or nearest["distance"] > 5:
            raise ValueError("HRRR station point invalid")
        return {
            "temperature_f": (kelvin - 273.15) * 1.8 + 32,
            "grid_latitude": nearest["lat"],
            "grid_longitude": nearest["lon"],
            "distance_km": nearest["distance"],
            "grid_index": nearest["index"],
        }
    finally:
        eccodes.codes_release(handle)


def collect(store, cycle):
    output = []
    with httpx.Client(timeout=30) as client:
        for hour in range(1, 19):
            url = (
                f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{cycle:%Y%m%d}/conus/"
                f"hrrr.t{cycle:%H}z.wrfsfcf{hour:02}.grib2"
            )
            started = time.time()
            index = client.get(url + ".idx")
            stamp = time.time()
            capture = store.capture("hrrr_index", url + ".idx", started, stamp, index.content)
            index.raise_for_status()
            lines = index.text.splitlines()
            selected = [i for i, line in enumerate(lines) if ":TMP:2 m above ground:" in line]
            if len(selected) != 1 or selected[0] + 1 >= len(lines):
                raise ValueError("Missing/ambiguous HRRR 2m field")
            i = selected[0]
            start, end = int(lines[i].split(":")[1]), int(lines[i + 1].split(":")[1]) - 1
            started = time.time()
            # Stream and stop if Range is ignored; never download a full CONUS file.
            with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
                if (
                    response.status_code != 206
                    or not 0 <= start < end
                    or end - start > 20_000_000
                    or not response.headers.get("Content-Range", "").startswith(
                        f"bytes {start}-{end}/"
                    )
                ):
                    raise ValueError("HRRR byte range unavailable")
                body = response.read()
            received = time.time()
            if len(body) != end - start + 1:
                raise ValueError("Truncated HRRR field")
            sample = point(body, cycle, hour)
            output.append(
                sample
                | {
                    "valid_at": (cycle + timedelta(hours=hour)).timestamp(),
                    "forecast_hour": hour,
                    "received_at": received,
                    "requested_at": started,
                    "url": url,
                    "byte_range": [start, end],
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "index_capture": capture,
                }
            )
    return {
        "station": "KNYC",
        "cycle": cycle.timestamp(),
        "received_at": time.time(),
        "points": output,
        "source": "NOAA_HRRR",
        "complete": len(output) == 18,
    }


async def worker(store, stop):
    last_cycle = None
    while not stop.is_set():
        cycle = (datetime.now(UTC) - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        if cycle != last_cycle:
            try:
                data = await asyncio.to_thread(collect, store, cycle)
                store.publish("weather", "hrrr", data, data["received_at"])
                store.publish("health", "hrrr", {"status": "connected", "received_at": time.time()})
                last_cycle = cycle
            except (ImportError, httpx.HTTPError, ValueError, RuntimeError):
                store.publish(
                    "health",
                    "hrrr",
                    {
                        "status": "unavailable",
                        "message": "HRRR decoder or current cycle unavailable",
                        "received_at": time.time(),
                    },
                )
        try:
            await asyncio.wait_for(stop.wait(), 300)
        except TimeoutError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(worker(FeedStore(args.root / "feed.sqlite3"), asyncio.Event()))
