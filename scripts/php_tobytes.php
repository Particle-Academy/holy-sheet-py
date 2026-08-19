<?php

declare(strict_types=1);

/**
 * Cross-runtime parity helper: emit xlsx bytes from the PHP holy-sheet for a
 * given JSON input, so the Python port can be diffed against it. Uses a
 * minimal PSR-4 autoloader (the PHP core is zero-dependency) - no composer
 * needed.
 *
 *   php php_tobytes.php <input.json> <out.xlsx>
 *
 * `HOLY_SHEET_PHP_SRC` is checked before the sibling checkout. A hard-coded sibling path
 * alone would resolve only inside the .agi envelope, so CI - or anyone with a
 * different layout - would silently get no parity run at all rather than an
 * error. That is precisely how the holy-sheet and dark-slide suites spent
 * months reporting green over zero cross-engine coverage.
 */

spl_autoload_register(function (string $class): void {
    $prefix = 'HolySheet\\';
    if (strncmp($class, $prefix, strlen($prefix)) !== 0) {
        return;
    }
    $rel = substr($class, strlen($prefix));
    $root = getenv('HOLY_SHEET_PHP_SRC') ?: __DIR__.'/../../holy-sheet/src';
    $file = rtrim($root, '/').'/'.str_replace('\\', '/', $rel).'.php';
    if (is_file($file)) {
        require $file;
    }
});

if ($argc < 3) {
    fwrite(STDERR, "usage: php php_tobytes.php <input.json> <out.xlsx>\n");
    exit(2);
}

$input = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);

// Fail loudly if the autoloader found nothing. Without this the script dies on
// "class not found" from deep inside the writer, which reads like a parity
// failure rather than a missing checkout.
if (! class_exists(\HolySheet\Agent::class)) {
    fwrite(STDERR, "HolySheet\\Agent not found. Set HOLY_SHEET_PHP_SRC to the PHP package's src/ directory.\n");
    exit(3);
}

file_put_contents($argv[2], \HolySheet\Agent::toBytes($input));
