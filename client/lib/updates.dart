import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

enum UpdateState { available, current, unavailable, unsupported }

class UpdateInfo {
  final UpdateState state;
  final String currentVersion;
  final String? latestVersion, assetUrl, assetName, releaseUrl, tag;
  final DateTime? checkedAt;
  final bool refreshFailed;
  const UpdateInfo({
    required this.state,
    required this.currentVersion,
    this.latestVersion,
    this.assetUrl,
    this.assetName,
    this.releaseUrl,
    this.tag,
    this.checkedAt,
    this.refreshFailed = false,
  });
  bool get hasUpdate => state == UpdateState.available && assetUrl != null;
}

/// Public GitHub metadata only. This service never receives application tokens,
/// school credentials, article content, or the application's authenticated API.
class UpdateService {
  static const storageKey = 'optinfo_public_update_v1';
  static const checkInterval = Duration(hours: 6);
  static const retryInterval = Duration(minutes: 15);
  static const releases =
      'https://api.github.com/repos/Leonids312/Optinfo/releases/latest';
  final http.Client _client;
  final DateTime Function() clock;
  final Future<String> Function() _versionLoader;
  final TargetPlatform platform;
  final bool web;
  Future<UpdateInfo>? _pending;
  _CheckRun? _run;
  UpdateInfo? _last;
  DateTime? _attempt;
  bool _succeeded = false;
  String? _context;

  UpdateService(
      {http.Client? client,
      DateTime Function()? clock,
      Future<String> Function()? versionLoader,
      TargetPlatform? platform,
      bool? web})
      : _client = client ?? http.Client(),
        clock = clock ?? DateTime.now,
        _versionLoader = versionLoader ??
            (() async => (await PackageInfo.fromPlatform()).version),
        platform = platform ?? defaultTargetPlatform,
        web = web ?? kIsWeb;

  Future<UpdateInfo> check({bool force = false}) {
    if (_pending != null) {
      final run = _run!;
      if (!force || run.network) return _pending!;
      run.force = true;
      return _pending!.then((result) {
        if (run.network || result.state == UpdateState.unsupported) {
          return result;
        }
        // A cached result may already be on its way through the microtask
        // queue. A manual check still must get a live request in that case.
        return check(force: true);
      });
    }
    final run = _CheckRun(force);
    _run = run;
    return _pending = _check(run).whenComplete(() {
      _pending = null;
      _run = null;
    });
  }

  Future<UpdateInfo> _check(_CheckRun run) async {
    String current = '';
    try {
      current =
          (await _versionLoader().timeout(const Duration(seconds: 5))).trim();
      if (_Version.parse(current) == null) {
        throw const FormatException('Unsupported installed version');
      }
      if (web ||
          ![TargetPlatform.android, TargetPlatform.windows]
              .contains(platform)) {
        return UpdateInfo(
            state: UpdateState.unsupported, currentVersion: current);
      }
      final context = '${platform.name}:$current';
      if (_context != context) {
        _context = context;
        _last = null;
        _attempt = null;
        _succeeded = false;
        await _restore(current);
      }
      final now = clock().toUtc();
      final elapsed = _attempt == null ? null : now.difference(_attempt!);
      if (!run.force &&
          _last != null &&
          elapsed != null &&
          !elapsed.isNegative &&
          elapsed < (_succeeded ? checkInterval : retryInterval)) {
        return _last!;
      }
      _attempt = now;
      try {
        run.network = true;
        final data = await _readRelease().timeout(const Duration(seconds: 12));
        _last = _parseRelease(data, current, now);
        _succeeded = true;
      } catch (_) {
        _succeeded = false;
        final known = _last;
        _last = known?.hasUpdate == true
            ? UpdateInfo(
                state: UpdateState.available,
                currentVersion: current,
                latestVersion: known!.latestVersion,
                assetUrl: known.assetUrl,
                assetName: known.assetName,
                releaseUrl: known.releaseUrl,
                tag: known.tag,
                checkedAt: known.checkedAt,
                refreshFailed: true)
            : UpdateInfo(
                state: UpdateState.unavailable,
                currentVersion: current,
                checkedAt: now);
      }
      await _persist();
      return _last!;
    } catch (_) {
      return UpdateInfo(
          state: UpdateState.unavailable, currentVersion: current);
    }
  }

  Future<Map<String, dynamic>> _readRelease() async {
    final request = http.Request('GET', Uri.parse(releases))
      ..followRedirects = false;
    request.headers.addAll({
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'Optinfo-Update-Check'
    });
    final response = await _client.send(request);
    if (response.statusCode != 200 ||
        !(response.headers['content-type'] ?? '')
            .contains('application/json')) {
      throw const FormatException('Release metadata unavailable');
    }
    final bytes = <int>[];
    await for (final chunk in response.stream) {
      if (bytes.length + chunk.length > 512 * 1024) {
        throw const FormatException('Release metadata too large');
      }
      bytes.addAll(chunk);
    }
    final value = jsonDecode(utf8.decode(bytes));
    if (value is! Map<String, dynamic>) {
      throw const FormatException('Invalid release');
    }
    return value;
  }

  UpdateInfo _parseRelease(
      Map<String, dynamic> value, String current, DateTime at) {
    final tag = value['tag_name'];
    final latest = tag is String ? _Version.parse(tag, tag: true) : null;
    if (value['draft'] != false ||
        value['prerelease'] != false ||
        latest == null) {
      throw const FormatException('Not a stable release');
    }
    final installed = _Version.parse(current)!;
    if (latest.compareTo(installed) <= 0) {
      return UpdateInfo(
          state: UpdateState.current,
          currentVersion: current,
          latestVersion: latest.text,
          checkedAt: at);
    }
    final name = platform == TargetPlatform.android
        ? 'Optinfo-Android-$tag.apk'
        : 'Optinfo-Windows-x64-$tag.zip';
    final assets = value['assets'];
    if (assets is! List) throw const FormatException('No platform package');
    final matching = assets
        .whereType<Map>()
        .where((a) => a['name'] == name && a['state'] == 'uploaded')
        .toList();
    if (matching.length != 1) {
      throw const FormatException('Ambiguous platform package');
    }
    final url = matching.single['browser_download_url'];
    final expected =
        'https://github.com/Leonids312/Optinfo/releases/download/$tag/$name';
    final uri = url is String ? Uri.tryParse(url) : null;
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host != 'github.com' ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        uri.port != 443 ||
        uri.toString() != expected) {
      throw const FormatException('Invalid package URL');
    }
    return UpdateInfo(
        state: UpdateState.available,
        currentVersion: current,
        latestVersion: latest.text,
        assetUrl: expected,
        assetName: name,
        tag: tag,
        releaseUrl: 'https://github.com/Leonids312/Optinfo/releases/tag/$tag',
        checkedAt: at);
  }

  Future<void> _restore(String current) async {
    try {
      final text =
          (await SharedPreferences.getInstance()).getString(storageKey);
      if (text == null || text.length > 8192) return;
      final doc = jsonDecode(text);
      if (doc is! Map || doc['context'] != _context) return;
      final attempt = DateTime.tryParse('${doc['attempt']}');
      if (attempt == null || attempt.isAfter(clock().toUtc())) return;
      final at = DateTime.tryParse('${doc['checked_at']}') ?? attempt;
      if (doc['tag'] != null) {
        _last = _parseRelease({
          'tag_name': doc['tag'],
          'draft': false,
          'prerelease': false,
          'assets': [
            {
              'name': doc['asset_name'],
              'state': 'uploaded',
              'browser_download_url': doc['asset_url']
            }
          ]
        }, current, at);
      } else if (doc['state'] == UpdateState.current.name) {
        final latest = _Version.parse('${doc['latest']}');
        if (latest == null || latest.compareTo(_Version.parse(current)!) > 0) {
          return;
        }
        _last = UpdateInfo(
            state: UpdateState.current,
            currentVersion: current,
            latestVersion: latest.text,
            checkedAt: at);
      } else {
        _last = UpdateInfo(
            state: UpdateState.unavailable,
            currentVersion: current,
            checkedAt: at);
      }
      _attempt = attempt;
      _succeeded = doc['success'] == true;
      if (!_succeeded && _last!.hasUpdate) {
        final known = _last!;
        _last = UpdateInfo(
            state: UpdateState.available,
            currentVersion: current,
            latestVersion: known.latestVersion,
            assetUrl: known.assetUrl,
            assetName: known.assetName,
            releaseUrl: known.releaseUrl,
            tag: known.tag,
            checkedAt: known.checkedAt,
            refreshFailed: true);
      }
    } catch (_) {/* A broken cache must not block a live update check. */}
  }

  Future<void> _persist() async {
    final info = _last;
    if (info == null) return;
    try {
      await (await SharedPreferences.getInstance()).setString(
          storageKey,
          jsonEncode({
            'context': _context,
            'attempt': _attempt?.toIso8601String(),
            'success': _succeeded,
            'state': info.state.name,
            'latest': info.latestVersion,
            'tag': info.tag,
            'asset_name': info.assetName,
            'asset_url': info.assetUrl,
            'checked_at': info.checkedAt?.toIso8601String(),
          }));
    } catch (_) {/* Public update metadata remains usable in memory. */}
  }

  void dispose() => _client.close();
}

class _CheckRun {
  bool force;
  bool network = false;
  _CheckRun(this.force);
}

class _Version implements Comparable<_Version> {
  final List<int> parts;
  _Version(this.parts);
  String get text => parts.join('.');
  static _Version? parse(String value, {bool tag = false}) {
    final pattern = tag
        ? r'^v?(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})$'
        : r'^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:\+[0-9A-Za-z.-]+)?$';
    final match = RegExp(pattern).firstMatch(value);
    return match == null
        ? null
        : _Version([for (var i = 1; i <= 3; i++) int.parse(match[i]!)]);
  }

  @override
  int compareTo(_Version other) {
    for (var i = 0; i < 3; i++) {
      final order = parts[i].compareTo(other.parts[i]);
      if (order != 0) return order;
    }
    return 0;
  }
}
