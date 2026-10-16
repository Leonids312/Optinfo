import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:optinfo/updates.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

Map<String, dynamic> release({String tag = 'v0.1.10'}) => {
      'tag_name': tag,
      'draft': false,
      'prerelease': false,
      'assets': [
        for (final name in [
          'Optinfo-Android-$tag.apk',
          'Optinfo-Windows-x64-$tag.zip'
        ])
          {
            'name': name,
            'state': 'uploaded',
            'browser_download_url':
                'https://github.com/Leonids312/Optinfo/releases/download/$tag/$name'
          }
      ],
    };
http.Response response(Map<String, dynamic> data) =>
    http.Response(jsonEncode(data), 200,
        headers: {'content-type': 'application/json'});

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test(
      'numeric stable versions select the exact platform asset without credentials',
      () async {
    for (final platform in [TargetPlatform.android, TargetPlatform.windows]) {
      final service = UpdateService(
          platform: platform,
          web: false,
          versionLoader: () async => '0.1.9+2',
          client: MockClient((request) async {
            expect(request.url.toString(), UpdateService.releases);
            expect(request.headers.containsKey('Authorization'), isFalse);
            expect(request.headers.containsKey('authorization'), isFalse);
            expect(request.followRedirects, isFalse);
            return response(release());
          }));
      final result = await service.check(force: true);
      expect(result.state, UpdateState.available);
      expect(result.latestVersion, '0.1.10');
      expect(
          result.assetName,
          platform == TargetPlatform.android
              ? 'Optinfo-Android-v0.1.10.apk'
              : 'Optinfo-Windows-x64-v0.1.10.zip');
      expect(result.assetUrl, endsWith(result.assetName!));
    }
  });

  test(
      'installed package version is read from the platform and never downgraded',
      () async {
    PackageInfo.setMockInitialValues(
        appName: 'Optinfo',
        packageName: 'org.leonids.optinfo',
        version: '0.2.0',
        buildNumber: '45',
        buildSignature: 'test');
    final service = UpdateService(
        platform: TargetPlatform.windows,
        web: false,
        client: MockClient((_) async => response(release())));
    final result = await service.check();
    expect(result.currentVersion, '0.2.0');
    expect(result.state, UpdateState.current);
    expect(result.assetUrl, isNull);
  });

  test(
      'success persists six-hour throttle; failed refresh keeps known update with short retry',
      () async {
    var now = DateTime.utc(2026, 10, 16);
    var calls = 0;
    var fail = false;
    UpdateService create() => UpdateService(
        platform: TargetPlatform.android,
        web: false,
        clock: () => now,
        versionLoader: () async => '0.1.0',
        client: MockClient((_) async {
          calls++;
          if (fail) throw http.ClientException('offline');
          return response(release());
        }));
    final first = create();
    expect((await first.check()).hasUpdate, isTrue);
    expect(calls, 1);
    fail = true;
    final reopened = create();
    expect((await reopened.check()).hasUpdate, isTrue);
    expect(calls, 1);
    now = now.add(const Duration(hours: 7));
    final failed = await reopened.check();
    expect(failed.hasUpdate, isTrue);
    expect(failed.refreshFailed, isTrue);
    expect(calls, 2);
    now = now.add(const Duration(minutes: 14));
    await reopened.check();
    expect(calls, 2);
    now = now.add(const Duration(minutes: 1));
    await reopened.check();
    expect(calls, 3);
    await reopened.check(force: true);
    expect(calls, 4);
    final cachedFailure = await create().check();
    expect(cachedFailure.hasUpdate, isTrue);
    expect(cachedFailure.refreshFailed, isTrue);
    expect(calls, 4);
  });

  test('manual check upgrades an automatic cache check to a network request',
      () async {
    var calls = 0;
    Future<String> Function() loader = () async => '0.1.0';
    final service = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () => loader(),
        client: MockClient((_) async {
          calls++;
          return response(release());
        }));
    await service.check();
    expect(calls, 1);
    final delayedVersion = Completer<String>();
    loader = () => delayedVersion.future;
    final automatic = service.check();
    final manual = service.check(force: true);
    delayedVersion.complete('0.1.0');
    await Future.wait([automatic, manual]);
    expect(calls, 2);
  });

  test('new installed version invalidates the previous update cache', () async {
    var version = '0.1.0';
    var calls = 0;
    UpdateService create() => UpdateService(
        platform: TargetPlatform.windows,
        web: false,
        versionLoader: () async => version,
        client: MockClient((_) async {
          calls++;
          return response(release());
        }));
    expect((await create().check()).hasUpdate, isTrue);
    version = '0.1.10';
    expect((await create().check()).state, UpdateState.current);
    expect(calls, 2);
  });

  test(
      'prereleases drafts malformed assets and untrusted download links are rejected',
      () async {
    final invalid = <Map<String, dynamic>>[
      release()..['prerelease'] = true,
      release()..['draft'] = true,
      release(tag: 'v0.2.0-beta.1'),
      release()..['assets'] = [],
      release()..['assets'] = [release()['assets'][0], release()['assets'][0]],
      for (final url in [
        'http://github.com/Leonids312/Optinfo/releases/download/v0.1.10/Optinfo-Android-v0.1.10.apk',
        'https://evil.invalid/Optinfo-Android-v0.1.10.apk',
        'https://github.com/another/Optinfo/releases/download/v0.1.10/Optinfo-Android-v0.1.10.apk',
        'https://github.com/Leonids312/Optinfo/releases/download/v0.1.10/other.apk',
        'https://user@github.com/Leonids312/Optinfo/releases/download/v0.1.10/Optinfo-Android-v0.1.10.apk',
        'https://github.com/Leonids312/Optinfo/releases/download/v0.1.10/Optinfo-Android-v0.1.10.apk?target=evil',
      ])
        release()..['assets'][0]['browser_download_url'] = url,
    ];
    for (final value in invalid) {
      SharedPreferences.setMockInitialValues({});
      final service = UpdateService(
          platform: TargetPlatform.android,
          web: false,
          versionLoader: () async => '0.1.0',
          client: MockClient((_) async => response(value)));
      final result = await service.check(force: true);
      expect(result.state, UpdateState.unavailable);
      expect(result.assetUrl, isNull);
    }
  });

  test(
      'redirects and rate limits fail quietly; concurrent checks share one request',
      () async {
    final delayed = Completer<http.Response>();
    var calls = 0;
    final service = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () async => '0.1.0',
        client: MockClient((_) {
          calls++;
          return delayed.future;
        }));
    final first = service.check();
    final second = service.check(force: true);
    delayed.complete(
        http.Response('', 302, headers: {'location': 'https://evil.invalid/'}));
    expect((await first).state, UpdateState.unavailable);
    expect((await second).state, UpdateState.unavailable);
    expect(calls, 1);
    final limited = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () async => '0.1.0',
        client: MockClient((_) async => http.Response('', 403)));
    expect((await limited.check(force: true)).state, UpdateState.unavailable);
  });

  test('web and unsupported platforms perform no release request', () async {
    for (final web in [false, true]) {
      final service = UpdateService(
          platform: web ? TargetPlatform.windows : TargetPlatform.linux,
          web: web,
          versionLoader: () async => '0.1.0',
          client:
              MockClient((_) async => throw StateError('Must not request')));
      expect((await service.check()).state, UpdateState.unsupported);
    }
  });

  test('oversized metadata is rejected and package-version failures stay quiet',
      () async {
    final oversized = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () async => '0.1.0',
        client: MockClient(
            (_) async => response(release()..['body'] = 'x' * (513 * 1024))));
    expect((await oversized.check()).state, UpdateState.unavailable);
    final missingVersion = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () async => throw StateError('Platform lookup failed'),
        client: MockClient((_) async => throw StateError('Must not request')));
    expect((await missingVersion.check()).state, UpdateState.unavailable);
  });

  test('corrupt local update data does not suppress a real check', () async {
    SharedPreferences.setMockInitialValues(
        {UpdateService.storageKey: 'not JSON'});
    var calls = 0;
    final service = UpdateService(
        platform: TargetPlatform.android,
        web: false,
        versionLoader: () async => '0.1.0',
        client: MockClient((_) async {
          calls++;
          return response(release());
        }));
    expect((await service.check()).hasUpdate, isTrue);
    expect(calls, 1);
  });
}
