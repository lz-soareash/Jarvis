const path = require('path');

const ICON = path.join(__dirname, '..', 'resources', 'icon.ico');
const METADATA = {
  CompanyName: 'VEGA',
  FileDescription: 'VEGA',
  ProductName: 'VEGA',
  InternalName: 'VEGA.exe',
  OriginalFilename: 'VEGA.exe'
};

async function afterPack(context) {
  const pkg = require('../package.json');
  const { rcedit } = await import('rcedit');
  const exe = path.join(context.appOutDir, 'VEGA.exe');
  const options = {
    icon: ICON,
    'file-version': pkg.version,
    'product-version': pkg.version,
    'version-string': METADATA
  };
  await rcedit(exe, options);
  console.log(`[after-pack] recursos de ${exe} atualizados (v${pkg.version})`);
}

module.exports = afterPack;