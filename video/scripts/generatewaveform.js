const fs = require("fs");
const path = require("path");
const ffmpeg = require("fluent-ffmpeg");

const input = process.argv[2];
const output = process.argv[3] || "public/waveform.json";

// how many samples you want (controls resolution)
const SAMPLES = 2000;

if (!input) {
  console.error("Usage: node generateWaveform.js <audio-file>");
  process.exit(1);
}

const tempRaw = path.join(__dirname, "temp.raw");

ffmpeg(input)
  .audioChannels(1)
  .audioFrequency(44100)
  .format("f32le") // raw float32 PCM
  .save(tempRaw)
  .on("end", () => {
    const buffer = fs.readFileSync(tempRaw);
    const samples = new Float32Array(
      buffer.buffer,
      buffer.byteOffset,
      buffer.length / 4
    );

    const blockSize = Math.floor(samples.length / SAMPLES);
    const waveform = [];

    for (let i = 0; i < SAMPLES; i++) {
      let sum = 0;
      let count = 0;

      for (let j = 0; j < blockSize; j++) {
        const val = samples[i * blockSize + j];
        if (val !== undefined) {
          sum += Math.abs(val);
          count++;
        }
      }

      waveform.push(count ? sum / count : 0);
    }

    // normalize
    const max = Math.max(...waveform);
    const normalized = waveform.map((v) => v / max);

    fs.writeFileSync(output, JSON.stringify(normalized));
    fs.unlinkSync(tempRaw);

    console.log(`✅ Waveform written to ${output}`);
  })
  .on("error", (err) => {
    console.error("FFmpeg error:", err);
  });