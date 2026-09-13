<?php

declare(strict_types=1);

/**
 * Cross-runtime READER parity helper: describe a spreadsheet file with the PHP
 * holy-sheet reader and print the schema as JSON, so the Python reader can be
 * diffed against it. Same autoloader and lookup order as php_tobytes.php.
 *
 *   php php_describe.php <in.xlsx|in.ods>
 *
 * JSON_PRESERVE_ZERO_FRACTION keeps a PHP float a float (`1500.0`, not `1500`),
 * because the int/float distinction is part of what is being compared.
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

if ($argc < 2) {
    fwrite(STDERR, "usage: php php_describe.php <file>\n");
    exit(2);
}

if (! class_exists(\HolySheet\Agent::class)) {
    fwrite(STDERR, "HolySheet\\Agent not found. Set HOLY_SHEET_PHP_SRC to the PHP package's src/ directory.\n");
    exit(3);
}

echo json_encode(\HolySheet\Agent::describe($argv[1]), JSON_PRESERVE_ZERO_FRACTION | JSON_THROW_ON_ERROR);
