#!/usr/bin/env node

/**
 * podcast-video render.js
 *
 * Usage:
 *   node scripts/render.js <audio.mp3> --ep <episode> [--title <title>] [--cover <cover.png>] [--duration <seconds>]
 *
 * Examples:
 *   node scripts/render.js episode.mp3 --ep 0003 --title "Daily Stoic"
 *   node scripts/render.js audio.mp3 --ep 0001 --title "My Show" --cover assets/cover.png --duration 3600
 */

const { bundle } = require('@remotion/bundler');
const { renderMedia, selectComposition } = require('@remotion/renderer');
const path = require('path');
const fs = require('fs');

// ─── Arg Parsing ────────────────────────────────────────────────────────────

const args = process.argv.slice(2);

if (args.length === 0 || args[0].startsWith('--')) {
    console.error('❌  Usage: node scripts/render.js <audio.mp3> [options]');
    console.error('   Options:');
    console.error('     --ep <episode>        Episode number (default: 0001)');
    console.error('     --title <title>       Episode title (default: Daily)');
    console.error('     --cover <path>        Cover image path (default: assets/cover.png)');
    console.error('     --duration <seconds>  Override duration (default: auto-detect)');
    process.exit(1);
}

const audioInput = args[0];

const getArg = (flag, fallback) => {
    const i = args.indexOf(flag);
    return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};

const episode = getArg('--ep', '0001');
const title = getArg('--title', 'Daily');
const cover = getArg('--cover', path.resolve('./assets/cover.png'));
const durationOverride = getArg('--duration', null);

// ─── Paths ───────────────────────────────────────────────────────────────────

const audioPath = path.resolve(audioInput);
const outputPath = path.resolve(`./out/episode-${episode}.mp4`);

// ─── Validation ──────────────────────────────────────────────────────────────

if (!fs.existsSync(audioPath)) {
    console.error(`❌  Audio file not found: ${audioPath}`);
    process.exit(1);
}

if (!fs.existsSync(cover)) {
    console.warn(`⚠️  Cover not found at ${cover} — using a placeholder`);
}

// Ensure output dir exists
fs.mkdirSync(path.dirname(outputPath), { recursive: true });

// ─── Duration Detection ───────────────────────────────────────────────────────

async function getAudioDuration(filePath) {
    if (durationOverride) {
        return parseFloat(durationOverride);
    }

    // Try to detect duration using ffprobe if available
    try {
        const { execSync } = require('child_process');
        const result = execSync(
            `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${filePath}"`,
            { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
        ).trim();
        const duration = parseFloat(result);
        if (!isNaN(duration) && duration > 0) {
            console.log(`🎵  Detected audio duration: ${Math.round(duration)}s (${(duration / 60).toFixed(1)} min)`);
            return duration;
        }
    } catch (_) {
        console.warn('⚠️  ffprobe not found — defaulting to 60min. Install ffmpeg or pass --duration <seconds>.');
    }

    return 3600; // fallback: 60 minutes
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function run() {
    console.log('\n🎙  podcast-video renderer');
    console.log('━'.repeat(40));
    console.log(`   Audio  : ${audioPath}`);
    console.log(`   Episode: ${episode}`);
    console.log(`   Title  : ${title}`);
    console.log(`   Cover  : ${cover}`);
    console.log(`   Output : ${outputPath}`);
    console.log('━'.repeat(40) + '\n');

    const publicDir = path.resolve("./public");
    fs.mkdirSync(publicDir, { recursive: true });

    // Copy assets into public so Remotion can serve them
    const audioName = path.basename(audioPath);
    const coverName = path.basename(cover);

    // fs.copyFileSync(audioPath, path.join(publicDir, audioName));
    // fs.copyFileSync(cover, path.join(publicDir, coverName));

    const durationInSeconds = await getAudioDuration(audioPath);
    const durationInFrames = Math.ceil(durationInSeconds * 30);

    console.log('📦  Bundling Remotion...');
    const bundleLocation = await bundle({
        entryPoint: path.resolve('./src/index.ts'),
        // Suppress verbose webpack output
        onBundleProgress: (progress) => {
            process.stdout.write(`\r   Bundle progress: ${Math.round(progress * 100)}%`);
        },
    });
    console.log('\n✅  Bundle complete\n');

    console.log('🔍  Resolving composition...');
    const composition = await selectComposition({
        serveUrl: bundleLocation,
        id: 'Podcast',
        inputProps: {
            audio: audioName,
            cover: coverName,
            title,
            episode,
            durationInSeconds,
        },
    });

    // Override duration with actual audio length
    const finalComposition = {
        ...composition,
        durationInFrames,
    };

    console.log(`🎬  Rendering ${durationInFrames} frames at 30fps (${(durationInFrames / 30 / 60).toFixed(1)} min)...\n`);

    await renderMedia({
        composition: finalComposition,
        serveUrl: bundleLocation,
        codec: 'h264',
        outputLocation: outputPath,
        inputProps: {
            audio: audioPath,
            cover,
            title,
            episode,
            durationInSeconds,
        },
        onProgress: ({ progress, renderedFrames, encodedFrames }) => {
            const pct = Math.round(progress * 100);
            const bar = '█'.repeat(Math.floor(pct / 5)) + '░'.repeat(20 - Math.floor(pct / 5));
            process.stdout.write(
                `\r   [${bar}] ${pct}%  rendered:${renderedFrames} encoded:${encodedFrames}`
            );
        },
    });

    console.log('\n\n✅  Render complete!');
    console.log(`📁  Output: ${outputPath}\n`);
}

run().catch((err) => {
    console.error('\n❌  Render failed:', err.message);
    process.exit(1);
});
