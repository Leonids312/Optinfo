import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/main.dart';
import 'package:optinfo/screens.dart';
import 'package:optinfo/updates.dart';

const installed = UpdateInfo(
  state: UpdateState.current,
  currentVersion: '3.2.1',
  latestVersion: '3.2.1',
);
const available = UpdateInfo(
  state: UpdateState.available,
  currentVersion: '3.2.1',
  latestVersion: '3.3.0',
  assetUrl:
      'https://github.com/Leonids312/Optinfo/releases/download/v3.3.0/Optinfo-Android-v3.3.0.apk',
  assetName: 'Optinfo-Android-v3.3.0.apk',
);

class FakeUpdates extends UpdateService {
  final List<bool> requests = [];
  UpdateInfo result;
  Completer<UpdateInfo>? pending;
  FakeUpdates(this.result);

  @override
  Future<UpdateInfo> check({bool force = false}) {
    requests.add(force);
    return pending?.future ?? Future.value(result);
  }
}

Future<void> captureUpdate(WidgetTester tester, String name) async {
  if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] != '1') return;
  final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('update-test-capture')));
  await tester.runAsync(() async {
    final image = await boundary.toImage();
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('../.ops/$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

void main() {
  testWidgets(
      'automatic checks run without a news connection and again on resume',
      (tester) async {
    final updates = FakeUpdates(available);
    await tester.pumpWidget(MaterialApp(
      home: HomeScreen(api: Api(ConnectionStore()), updates: updates),
    ));
    await tester.pumpAndSettle();

    expect(find.text('连接你的私人资讯库'), findsOneWidget);
    expect(find.byType(UpdateAvailableNotice), findsOneWidget);
    expect(find.text('下载安卓版'), findsOneWidget);
    expect(updates.requests, [false]);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(updates.requests, [false, false]);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'a slow or failed automatic check never blocks the reading screen',
      (tester) async {
    final updates = FakeUpdates(installed)..pending = Completer<UpdateInfo>();
    await tester.pumpWidget(MaterialApp(
      home: HomeScreen(api: Api(ConnectionStore()), updates: updates),
    ));
    await tester.pump();
    expect(find.text('连接你的私人资讯库'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    updates.pending!.complete(const UpdateInfo(
      state: UpdateState.unavailable,
      currentVersion: '3.2.1',
    ));
    await tester.pumpAndSettle();
    expect(find.byType(UpdateAvailableNotice), findsNothing);
    expect(find.text('暂时无法检查更新，请稍后重试。'), findsNothing);
    expect(find.text('连接你的私人资讯库'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'settings shows actual versions and explicitly forces a manual check',
      (tester) async {
    tester.view.physicalSize = const Size(800, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final updates = FakeUpdates(available);
    UpdateInfo? notified;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: ConnectionDialog(
          api: Api(ConnectionStore()),
          updates: updates,
          initialUpdate: installed,
          onUpdate: (info) => notified = info,
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('当前版本 3.2.1'), findsOneWidget);
    expect(find.text('最新稳定版 3.2.1，当前版本无需更新。'), findsOneWidget);
    expect(updates.requests, isEmpty);

    await tester.tap(find.text('检查更新'));
    await tester.pumpAndSettle();
    expect(updates.requests, [true]);
    expect(find.text('发现新版本 3.3.0。'), findsOneWidget);
    expect(find.text('下载安卓版'), findsOneWidget);
    expect(notified, same(available));

    await tester.tap(find.text('开源许可'));
    await tester.pumpAndSettle();
    expect(find.text('3.2.1'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'a failed manual refresh retains the confirmed download in settings',
      (tester) async {
    tester.view.physicalSize = const Size(360, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final updates = FakeUpdates(UpdateInfo(
      state: UpdateState.available,
      currentVersion: '3.2.1',
      latestVersion: '3.3.0',
      assetName: 'Optinfo-Android-v3.3.0.apk',
      assetUrl: available.assetUrl,
      refreshFailed: true,
    ));
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: ConnectionDialog(
          api: Api(ConnectionStore()),
          updates: updates,
          initialUpdate: available,
        ),
      ),
    ));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('检查更新'));
    await tester.tap(find.text('检查更新'));
    await tester.pumpAndSettle();
    expect(find.text('本次未能刷新，仍可下载已确认的新版本 3.3.0。'), findsOneWidget);
    expect(find.text('下载安卓版'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('download notice fits a narrow screen and opens only on a tap',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var clicks = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Padding(
          padding: const EdgeInsets.all(24),
          child: UpdateAvailableNotice(
            info: available,
            onDownload: () => clicks++,
          ),
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(clicks, 0);
    await tester.tap(find.text('下载安卓版'));
    await tester.pump();
    expect(clicks, 1);
    expect(tester.takeException(), isNull);
  });

  for (final (size, scale, name) in [
    (const Size(800, 1100), 1.0, 'update-settings'),
    (const Size(360, 800), 2.0, 'update-settings-large-text'),
  ]) {
    testWidgets('settings remains scrollable at $scale text scale',
        (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] == '1') {
        await (FontLoader('NotoSansSC')
              ..addFont(
                  rootBundle.load('assets/fonts/NotoSansSC-Variable.ttf')))
            .load();
        await (FontLoader('MaterialIcons')
              ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
            .load();
      }
      final updates = FakeUpdates(available);
      final api = Api(ConnectionStore());
      await tester.pumpWidget(RepaintBoundary(
        key: const ValueKey('update-test-capture'),
        child: Builder(builder: (context) {
          final app = OptinfoApp(connection: api.connection).build(context)
              as MaterialApp;
          return MaterialApp(
            debugShowCheckedModeBanner: false,
            theme: app.theme,
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(context)
                  .copyWith(textScaler: TextScaler.linear(scale)),
              child: child!,
            ),
            home: Scaffold(
              body: ConnectionDialog(
                api: api,
                updates: updates,
                initialUpdate: installed,
              ),
            ),
          );
        }),
      ));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('检查更新'));
      await tester.pumpAndSettle();
      expect(find.text('当前版本 3.2.1'), findsOneWidget);
      expect(find.text('检查更新').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      await captureUpdate(tester, name);

      await tester.tap(find.text('检查更新'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('下载安卓版'));
      await tester.pumpAndSettle();
      expect(find.text('下载安卓版').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      await captureUpdate(tester, '$name-available');
    });
  }
}
